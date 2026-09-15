// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.13;

import {Test} from "forge-std/Test.sol";
import {ReserveConstitutionV0} from "../src/ReserveConstitutionV0.sol";
import {ReserveVaultV0} from "../src/ReserveVaultV0.sol";

/// @dev Test-only ERC-20. Minting and status setters are unrestricted for test setup.
contract MockReserveERC20 {
    string public constant name = "Mock USDC";
    string public constant symbol = "mUSDC";
    uint8 public immutable decimals;
    uint256 public totalSupply;
    bool public paused;
    mapping(address => bool) public isBlacklisted;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 amount);
    event Approval(address indexed owner, address indexed spender, uint256 amount);

    constructor(uint8 decimalScale) {
        decimals = decimalScale;
    }

    function setPaused(bool value) external {
        paused = value;
    }

    function setBlacklisted(address account, bool value) external {
        isBlacklisted[account] = value;
    }

    function mint(address to, uint256 amount) external {
        require(to != address(0), "Zero recipient");
        totalSupply += amount;
        balanceOf[to] += amount;
        emit Transfer(address(0), to, amount);
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        _transfer(msg.sender, to, amount);
        return true;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        require(!isBlacklisted[msg.sender], "Blacklisted");
        allowance[from][msg.sender] -= amount;
        _transfer(from, to, amount);
        return true;
    }

    function _transfer(address from, address to, uint256 amount) internal {
        require(!paused, "Paused");
        require(!isBlacklisted[from] && !isBlacklisted[to], "Blacklisted");
        require(to != address(0), "Zero recipient");
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        emit Transfer(from, to, amount);
    }
}

contract ReserveVaultV0Test is Test {
    MockReserveERC20 public token;
    MockReserveERC20 public liabilityToken;
    ReserveConstitutionV0 public constitution;
    ReserveVaultV0 public vault;

    function setUp() public {
        token = new MockReserveERC20(6);
        liabilityToken = new MockReserveERC20(6);
        constitution = new ReserveConstitutionV0();
        vault = new ReserveVaultV0(address(token), address(constitution), address(liabilityToken));
    }

    function test_ConstructorStoresDependencies() public view {
        assertEq(address(vault.reserveToken()), address(token));
        assertEq(address(vault.constitution()), address(constitution));
        assertEq(address(vault.liabilityToken()), address(liabilityToken));
    }

    function test_ConstructorRejectsMissingTokenCode() public {
        vm.expectRevert(ReserveVaultV0.InvalidReserveToken.selector);
        new ReserveVaultV0(address(0), address(constitution), address(liabilityToken));
        vm.expectRevert(ReserveVaultV0.InvalidReserveToken.selector);
        new ReserveVaultV0(address(0x1234), address(constitution), address(liabilityToken));
    }

    function test_ConstructorRejectsMissingConstitutionCode() public {
        vm.expectRevert(ReserveVaultV0.InvalidConstitution.selector);
        new ReserveVaultV0(address(token), address(0), address(liabilityToken));
        vm.expectRevert(ReserveVaultV0.InvalidConstitution.selector);
        new ReserveVaultV0(address(token), address(0x1234), address(liabilityToken));
    }

    function test_ActualDepositsIncreaseBacking() public {
        liabilityToken.mint(address(this), 100e6);
        token.mint(address(this), 100e6);
        assertEq(vault.liquidReserveBalance(), 0, "Caller funds are not vault reserves");
        assertTrue(token.transfer(address(vault), 60e6));
        assertEq(vault.liquidReserveBalance(), 60e6);
        vm.expectRevert(ReserveConstitutionV0.InsufficientBacking.selector);
        vault.validateReserveState();
        assertTrue(token.transfer(address(vault), 40e6));
        assertEq(vault.liquidReserveBalance(), 100e6);
        vault.validateReserveState();
    }

    function test_ValidationForwardsObservedBalanceAndZeroAave() public {
        liabilityToken.mint(address(this), 90e6);
        vm.expectCall(address(liabilityToken), abi.encodeCall(liabilityToken.totalSupply, ()));
        _deposit(100e6);
        vm.expectCall(address(token), abi.encodeCall(token.balanceOf, (address(vault))));
        vm.expectCall(address(constitution), abi.encodeCall(constitution.validateReserveState, (100e6, 90e6, 100e6, 0)));
        vault.validateReserveState();
    }

    function test_FabricatedBackingCannotCoverLiabilities() public {
        liabilityToken.mint(address(this), 1000e6);
        _deposit(100e6);
        // The standalone validator accepts invented amounts; the vault derives both sides internally.
        constitution.validateReserveState(1000e6, 1000e6, 1000e6, 0);
        vm.expectRevert(ReserveConstitutionV0.InsufficientBacking.selector);
        vault.validateReserveState();
    }

    function test_NoCallerSuppliedBackingOverload() public view {
        (bool accepted,) = address(vault)
            .staticcall(
                abi.encodeWithSignature("validateReserveState(uint256,uint256,uint256,uint256)", 100e6, 100e6, 100e6, 0)
            );
        assertFalse(accepted);
    }

    function test_SufficientBackingPasses() public {
        liabilityToken.mint(address(this), 90e6);
        _deposit(100e6);
        vault.validateReserveState();
        liabilityToken.mint(address(this), 10e6);
        vault.validateReserveState();
    }

    function test_LiabilitiesAboveActualReservesFail() public {
        liabilityToken.mint(address(this), 100e6 + 1);
        _deposit(100e6);
        vm.expectRevert(ReserveConstitutionV0.InsufficientBacking.selector);
        vault.validateReserveState();
    }

    function test_ZeroReservesZeroLiabilitiesPasses() public view {
        assertEq(vault.liquidReserveBalance(), 0);
        vault.validateReserveState();
    }

    function test_ZeroReservesPositiveLiabilitiesFails() public {
        liabilityToken.mint(address(this), 1);
        vm.expectRevert(ReserveConstitutionV0.InsufficientBacking.selector);
        vault.validateReserveState();
    }

    function test_UnrelatedTokenDoesNotCountAsBacking() public {
        liabilityToken.mint(address(this), 1);
        MockReserveERC20 unrelated = new MockReserveERC20(6);
        unrelated.mint(address(this), 100e6);
        assertTrue(unrelated.transfer(address(vault), 100e6));
        assertEq(vault.liquidReserveBalance(), 0);
        vm.expectRevert(ReserveConstitutionV0.InsufficientBacking.selector);
        vault.validateReserveState();
    }

    function test_MaximumReserveBalancePasses() public {
        liabilityToken.mint(address(this), type(uint256).max);
        _deposit(type(uint256).max);
        assertEq(vault.liquidReserveBalance(), type(uint256).max);
        vault.validateReserveState();
    }

    function testFuzz_ValidationMatchesUsableReserves(
        uint256 reserves,
        uint256 liabilities,
        bool paused,
        bool blacklisted
    ) public {
        liabilityToken.mint(address(this), liabilities);
        _deposit(reserves);
        token.setPaused(paused);
        token.setBlacklisted(address(vault), blacklisted);
        uint256 usable = paused || blacklisted ? 0 : reserves;
        assertEq(vault.nominalReserveBalance(), reserves);
        assertEq(vault.liquidReserveBalance(), usable);
        if (liabilities > usable) {
            vm.expectRevert(ReserveConstitutionV0.InsufficientBacking.selector);
        }
        vault.validateReserveState();
    }

    function test_ConstructorRejectsMissingLiabilityTokenCode() public {
        vm.expectRevert(ReserveVaultV0.InvalidLiabilityToken.selector);
        new ReserveVaultV0(address(token), address(constitution), address(0));
        vm.expectRevert(ReserveVaultV0.InvalidLiabilityToken.selector);
        new ReserveVaultV0(address(token), address(constitution), address(0x1234));
    }

    function test_ConstructorRejectsMismatchedDecimals() public {
        MockReserveERC20 differentScale = new MockReserveERC20(18);
        vm.expectRevert(ReserveVaultV0.DecimalScaleMismatch.selector);
        new ReserveVaultV0(address(token), address(constitution), address(differentScale));
        vm.expectRevert(ReserveVaultV0.DecimalScaleMismatch.selector);
        new ReserveVaultV0(address(differentScale), address(constitution), address(liabilityToken));
    }

    function test_MatchingNonSixDecimalsPasses() public {
        MockReserveERC20 reserve18 = new MockReserveERC20(18);
        MockReserveERC20 liability18 = new MockReserveERC20(18);
        ReserveVaultV0 vault18 = new ReserveVaultV0(address(reserve18), address(constitution), address(liability18));
        reserve18.mint(address(vault18), 1e18);
        liability18.mint(address(this), 1e18);
        vault18.validateReserveState();
    }

    function test_IncreasingSupplyAutomaticallyIncreasesLiabilities() public {
        _deposit(100e6);
        liabilityToken.mint(address(this), 100e6);
        vault.validateReserveState();
        // Supply held by another account still counts toward the vault's liabilities.
        liabilityToken.mint(address(0xBEEF), 1);
        vm.expectRevert(ReserveConstitutionV0.InsufficientBacking.selector);
        vault.validateReserveState();
    }

    function test_CallersCannotUnderstateLiabilities() public {
        _deposit(100e6);
        liabilityToken.mint(address(this), 100e6 + 1);
        vm.prank(address(0xBEEF));
        vm.expectRevert(ReserveConstitutionV0.InsufficientBacking.selector);
        vault.validateReserveState();
        (bool accepted,) = address(vault).staticcall(abi.encodeWithSignature("validateReserveState(uint256)", 0));
        assertFalse(accepted, "Old caller-supplied liabilities API must not exist");
        // Extra calldata on the new selector cannot replace the internally read supply either.
        (bool acceptedWithExtraData, bytes memory result) =
            address(vault).staticcall(abi.encodePacked(vault.validateReserveState.selector, abi.encode(uint256(0))));
        assertFalse(acceptedWithExtraData);
        assertEq(result, abi.encodeWithSelector(ReserveConstitutionV0.InsufficientBacking.selector));
    }

    function test_OperationalTokenHasNominalAndUsableReserves() public {
        _deposit(100e6);
        liabilityToken.mint(address(this), 100e6);
        assertEq(vault.nominalReserveBalance(), 100e6);
        assertEq(vault.liquidReserveBalance(), 100e6);
        vault.validateReserveState();
    }

    function test_PauseRemovesLiquidityAndUnpauseRestoresIt() public {
        _deposit(100e6);
        liabilityToken.mint(address(this), 100e6);
        token.setPaused(true);
        _assertFrozenReserves();
        vm.prank(address(vault));
        vm.expectRevert(bytes("Paused"));
        token.transfer(address(this), 1);
        token.setPaused(false);
        assertEq(vault.nominalReserveBalance(), 100e6);
        assertEq(vault.liquidReserveBalance(), 100e6);
        vault.validateReserveState();
    }

    function test_BlacklistRemovesLiquidityAndUnblacklistRestoresIt() public {
        _deposit(100e6);
        liabilityToken.mint(address(this), 100e6);
        token.setBlacklisted(address(vault), true);
        _assertFrozenReserves();
        vm.prank(address(vault));
        vm.expectRevert(bytes("Blacklisted"));
        token.transfer(address(this), 1);
        token.setBlacklisted(address(vault), false);
        assertEq(vault.nominalReserveBalance(), 100e6);
        assertEq(vault.liquidReserveBalance(), 100e6);
        vault.validateReserveState();
    }

    function test_BothControlsMustBeClearedToRestoreLiquidity() public {
        _deposit(100e6);
        liabilityToken.mint(address(this), 100e6);
        token.setPaused(true);
        token.setBlacklisted(address(vault), true);
        _assertFrozenReserves();
        token.setPaused(false);
        _assertFrozenReserves();
        token.setPaused(true);
        token.setBlacklisted(address(vault), false);
        _assertFrozenReserves();
        token.setPaused(false);
        assertEq(vault.liquidReserveBalance(), 100e6);
        vault.validateReserveState();
    }

    function test_BlacklistedCallerDoesNotRemoveVaultLiquidity() public {
        _deposit(100e6);
        liabilityToken.mint(address(this), 100e6);
        token.setBlacklisted(address(this), true);
        assertEq(vault.liquidReserveBalance(), 100e6);
        vault.validateReserveState();
    }

    function test_FrozenReservesWithZeroSupplyPasses() public {
        _deposit(100e6);
        token.setPaused(true);
        assertEq(vault.nominalReserveBalance(), 100e6);
        assertEq(vault.liquidReserveBalance(), 0);
        vault.validateReserveState();
        token.setPaused(false);
        token.setBlacklisted(address(vault), true);
        assertEq(vault.liquidReserveBalance(), 0);
        vault.validateReserveState();
    }

    function test_StatusReadFailuresRevert() public {
        _deposit(100e6);
        liabilityToken.mint(address(this), 100e6);
        bytes[2] memory queries =
            [abi.encodeCall(token.paused, ()), abi.encodeCall(token.isBlacklisted, (address(vault)))];
        for (uint256 i = 0; i < queries.length; i++) {
            vm.mockCallRevert(address(token), queries[i], hex"deadbeef");
            assertEq(vault.nominalReserveBalance(), 100e6);
            vm.expectRevert(bytes(hex"deadbeef"));
            vault.liquidReserveBalance();
            vm.expectRevert(bytes(hex"deadbeef"));
            vault.validateReserveState();
            vm.clearMockedCalls();
        }
    }

    function test_MalformedStatusResponsesRevert() public {
        _deposit(100e6);
        bytes[2] memory queries =
            [abi.encodeCall(token.paused, ()), abi.encodeCall(token.isBlacklisted, (address(vault)))];
        for (uint256 i = 0; i < queries.length; i++) {
            vm.mockCall(address(token), queries[i], hex"");
            assertEq(vault.nominalReserveBalance(), 100e6);
            vm.expectRevert();
            vault.liquidReserveBalance();
            vm.expectRevert();
            vault.validateReserveState();
            vm.clearMockedCalls();
        }
    }

    function _assertFrozenReserves() internal {
        assertEq(vault.nominalReserveBalance(), 100e6);
        assertEq(vault.liquidReserveBalance(), 0);
        assertEq(liabilityToken.totalSupply(), 100e6);
        vm.expectCall(address(constitution), abi.encodeCall(constitution.validateReserveState, (0, 100e6, 0, 0)));
        vm.expectRevert(ReserveConstitutionV0.InsufficientBacking.selector);
        vault.validateReserveState();
    }

    function _deposit(uint256 amount) internal {
        token.mint(address(this), amount);
        assertTrue(token.transfer(address(vault), amount));
    }
}
