// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.13;

import {Test} from "forge-std/Test.sol";
import {ReserveConstitutionV0} from "../src/ReserveConstitutionV0.sol";
import {ReserveVaultV0} from "../src/ReserveVaultV0.sol";

/// @dev Test-only ERC-20. Minting is unrestricted to construct test balances.
contract MockReserveERC20 {
    string public constant name = "Mock USDC";
    string public constant symbol = "mUSDC";
    uint8 public immutable decimals;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 amount);
    event Approval(address indexed owner, address indexed spender, uint256 amount);

    constructor(uint8 decimalScale) {
        decimals = decimalScale;
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
        allowance[from][msg.sender] -= amount;
        _transfer(from, to, amount);
        return true;
    }

    function _transfer(address from, address to, uint256 amount) internal {
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

    function testFuzz_ValidationMatchesActualReserves(uint256 reserves, uint256 liabilities) public {
        liabilityToken.mint(address(this), liabilities);
        _deposit(reserves);
        assertEq(vault.liquidReserveBalance(), reserves);
        if (liabilities > reserves) {
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

    function _deposit(uint256 amount) internal {
        token.mint(address(this), amount);
        assertTrue(token.transfer(address(vault), amount));
    }
}
