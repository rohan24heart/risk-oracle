// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.13;

import {Test} from "forge-std/Test.sol";
import {StdStorage, stdStorage} from "forge-std/StdStorage.sol";
import {ReserveConstitutionV0} from "../src/ReserveConstitutionV0.sol";
import {ReserveVaultV0, IReserveTokenV0, IAaveTokenV0} from "../src/ReserveVaultV0.sol";

interface IBaseUSDCFork is IReserveTokenV0 {
    function approve(address spender, uint256 amount) external returns (bool);
}

interface IAaveAddressesProviderFork {
    function getPool() external view returns (address);
    function getPoolDataProvider() external view returns (address);
}

interface IAaveDataProviderFork {
    function getReserveTokensAddresses(address asset) external view returns (address, address, address);
}

interface IAavePoolFork {
    function ADDRESSES_PROVIDER() external view returns (address);
    function supply(address asset, uint256 amount, address onBehalfOf, uint16 referralCode) external;
}

interface IAaveTokenFork is IAaveTokenV0 {
    function POOL() external view returns (address);
}

/// @dev Test-only liability accounting with controllable supply; never deployed to a live network.
contract BaseForkLiabilityTokenMock {
    uint8 public constant decimals = 6;
    uint256 public totalSupply;

    function setTotalSupply(uint256 amount) external {
        totalSupply = amount;
    }
}

contract ReserveVaultV0BaseForkTest is Test {
    using stdStorage for StdStorage;

    // Circle: https://developers.circle.com/stablecoins/usdc-contract-addresses
    address internal constant BASE_USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    // Official Aave address book: https://github.com/aave-dao/aave-address-book/blob/main/src/AaveV3Base.sol
    address internal constant AAVE_ADDRESSES_PROVIDER = 0xe20fCBdBfFC4Dd138cE8b2E6FBb6CB49777ad64D;
    address internal constant EXPECTED_POOL = 0xA238Dd80C259a72e81d7e4664a9801593F98d1c5;
    address internal constant EXPECTED_DATA_PROVIDER = 0x0F43731EB8d45A581f4a36DD74F5f358bc90C73A;
    uint256 internal constant BASE_FORK_BLOCK = 51_360_621;

    IBaseUSDCFork internal usdc;
    IAavePoolFork internal pool;
    IAaveTokenFork internal aToken;
    ReserveConstitutionV0 internal constitution;
    BaseForkLiabilityTokenMock internal liabilities;
    ReserveVaultV0 internal vault;

    /// @dev Requires BASE_RPC_URL. All deployments, storage writes, approvals and supplies are fork-local.
    function setUp() public {
        vm.createSelectFork(vm.envString("BASE_RPC_URL"), BASE_FORK_BLOCK);
        assertEq(block.chainid, 8453, "Expected Base mainnet");
        assertEq(block.number, BASE_FORK_BLOCK);
        assertGt(BASE_USDC.code.length, 0, "Native USDC must have deployed code");
        usdc = IBaseUSDCFork(BASE_USDC);
        assertEq(usdc.decimals(), 6, "Native USDC must use six decimals");
        assertFalse(usdc.paused(), "Pinned block must have operational USDC");

        IAaveAddressesProviderFork provider = IAaveAddressesProviderFork(AAVE_ADDRESSES_PROVIDER);
        address poolAddress = provider.getPool();
        address dataProviderAddress = provider.getPoolDataProvider();
        assertEq(poolAddress, EXPECTED_POOL);
        assertEq(dataProviderAddress, EXPECTED_DATA_PROVIDER);
        assertGt(poolAddress.code.length, 0);
        assertGt(dataProviderAddress.code.length, 0);
        pool = IAavePoolFork(poolAddress);
        assertEq(pool.ADDRESSES_PROVIDER(), AAVE_ADDRESSES_PROVIDER);

        // Resolve the native USDC aToken from real on-chain reserve metadata; no hardcoded aToken.
        (address aTokenAddress,,) = IAaveDataProviderFork(dataProviderAddress).getReserveTokensAddresses(BASE_USDC);
        assertGt(aTokenAddress.code.length, 0);
        aToken = IAaveTokenFork(aTokenAddress);
        assertEq(aToken.UNDERLYING_ASSET_ADDRESS(), BASE_USDC);
        assertEq(aToken.POOL(), poolAddress);
        assertEq(aToken.decimals(), 6);

        constitution = new ReserveConstitutionV0();
        liabilities = new BaseForkLiabilityTokenMock();
        vault = new ReserveVaultV0(BASE_USDC, address(constitution), address(liabilities), aTokenAddress);
        assertFalse(usdc.isBlacklisted(address(vault)), "Local vault must not be blacklisted");
        assertEq(aToken.balanceOf(address(vault)), 0);
    }

    function test_BaseNativeUSDCIntegration() public {
        assertEq(liabilities.decimals(), 6);
        assertEq(usdc.balanceOf(address(vault)), 0);
        assertEq(vault.nominalReserveBalance(), 0);
        assertEq(vault.liquidReserveBalance(), 0);
        assertEq(vault.aaveReserveBalance(), 0);
        assertEq(vault.totalBacking(), 0);
        vault.validateReserveState();

        uint256 reserves = 100e6;
        _assignUSDC(reserves);
        assertEq(usdc.balanceOf(address(vault)), reserves);
        assertEq(vault.nominalReserveBalance(), usdc.balanceOf(address(vault)));
        assertFalse(usdc.paused(), "Balance assignment must preserve pause status");
        assertFalse(usdc.isBlacklisted(address(vault)), "Balance assignment must preserve blacklist status");
        vm.expectCall(BASE_USDC, abi.encodeCall(usdc.paused, ()));
        vm.expectCall(BASE_USDC, abi.encodeCall(usdc.isBlacklisted, (address(vault))));
        vm.expectCall(BASE_USDC, abi.encodeCall(usdc.balanceOf, (address(vault))));
        assertEq(vault.liquidReserveBalance(), reserves);

        liabilities.setTotalSupply(90e6);
        vm.expectCall(
            address(constitution), abi.encodeCall(constitution.validateReserveState, (reserves, 90e6, reserves, 0))
        );
        vault.validateReserveState();
        liabilities.setTotalSupply(reserves);
        vault.validateReserveState();
        liabilities.setTotalSupply(reserves + 1);
        vm.expectRevert(ReserveConstitutionV0.InsufficientBacking.selector);
        vault.validateReserveState();
    }

    function test_RealAaveSupplyChangesSplitAndEnforcesCap() public {
        _assignUSDC(100e6);
        liabilities.setTotalSupply(90e6);
        uint256 initialBacking = vault.totalBacking();
        assertEq(initialBacking, 100e6);
        assertEq(vault.aaveReserveBalance(), 0);
        vault.validateReserveState();

        uint256 aTokenUnderlyingBefore = usdc.balanceOf(address(aToken));
        _supplyFromVault(30e6);
        assertEq(usdc.balanceOf(address(aToken)), aTokenUnderlyingBefore + 30e6);
        assertEq(vault.nominalReserveBalance(), 70e6);
        assertEq(vault.liquidReserveBalance(), 70e6);
        uint256 exposure = aToken.balanceOf(address(vault));
        assertGt(exposure, 0);
        assertEq(vault.aaveReserveBalance(), exposure);
        // Aave's scaled principal/index conversion can round by one smallest USDC unit per supply.
        assertApproxEqAbs(exposure, 30e6, 1);
        assertEq(vault.totalBacking(), 70e6 + exposure);
        assertApproxEqAbs(vault.totalBacking(), initialBacking, 1);
        vm.expectCall(
            address(constitution),
            abi.encodeCall(constitution.validateReserveState, (70e6 + exposure, 90e6, 70e6, exposure))
        );
        vault.validateReserveState();
        emit log_named_uint("Idle USDC after first supply", vault.liquidReserveBalance());
        emit log_named_uint("Aave USDC after first supply", exposure);
        emit log_named_uint("Backing after first supply", vault.totalBacking());

        _supplyFromVault(20e6);
        uint256 excessiveExposure = aToken.balanceOf(address(vault));
        assertEq(vault.liquidReserveBalance(), 50e6);
        assertGt(excessiveExposure, exposure);
        assertApproxEqAbs(excessiveExposure, 50e6, 2);
        assertEq(vault.totalBacking(), 50e6 + excessiveExposure);
        assertApproxEqAbs(vault.totalBacking(), initialBacking, 2);
        assertGt(excessiveExposure * 10_000, vault.totalBacking() * 4_000);
        vm.expectCall(
            address(constitution),
            abi.encodeCall(constitution.validateReserveState, (50e6 + excessiveExposure, 90e6, 50e6, excessiveExposure))
        );
        vm.expectRevert(ReserveConstitutionV0.ExcessiveAaveExposure.selector);
        vault.validateReserveState();

        emit log_named_uint("Base fork block", block.number);
        emit log_named_address("Native USDC", BASE_USDC);
        emit log_named_address("Aave addresses provider", AAVE_ADDRESSES_PROVIDER);
        emit log_named_address("Aave Pool", address(pool));
        emit log_named_address("Aave data provider", EXPECTED_DATA_PROVIDER);
        emit log_named_address("Resolved native USDC aToken", address(aToken));
        emit log_named_uint("Aave USDC above cap", excessiveExposure);
    }

    function _assignUSDC(uint256 amount) internal {
        // Only the idle USDC balance is assigned; aToken balances are created by real Pool.supply calls.
        stdstore.target(BASE_USDC)
            .sig(usdc.balanceOf.selector)
            .with_key(address(vault))
            .enable_packed_slots()
            .checked_write(amount);
    }

    function _supplyFromVault(uint256 amount) internal {
        // Impersonation exists only in this fork test. Production vault exposes no approvals or supply actions.
        vm.startPrank(address(vault));
        assertTrue(usdc.approve(address(pool), amount));
        pool.supply(BASE_USDC, amount, address(vault), 0);
        vm.stopPrank();
    }
}
