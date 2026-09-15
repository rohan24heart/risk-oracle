// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.13;

import {Test} from "forge-std/Test.sol";
import {StdStorage, stdStorage} from "forge-std/StdStorage.sol";
import {ReserveConstitutionV0} from "../src/ReserveConstitutionV0.sol";
import {ReserveVaultV0, IReserveTokenV0} from "../src/ReserveVaultV0.sol";

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

    // Circle's native Base USDC: https://developers.circle.com/stablecoins/usdc-contract-addresses
    address internal constant BASE_USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    uint256 internal constant BASE_FORK_BLOCK = 51_360_621;

    /// @dev Requires BASE_RPC_URL in the environment. All deployments and writes are fork-local.
    function test_BaseNativeUSDCIntegration() public {
        vm.createSelectFork(vm.envString("BASE_RPC_URL"), BASE_FORK_BLOCK);
        assertEq(block.chainid, 8453, "Expected Base mainnet");
        assertEq(block.number, BASE_FORK_BLOCK);
        assertGt(BASE_USDC.code.length, 0, "Native USDC must have deployed code");
        emit log_named_uint("Base fork block", block.number);
        emit log_named_address("Native USDC", BASE_USDC);

        IReserveTokenV0 usdc = IReserveTokenV0(BASE_USDC);
        assertEq(usdc.decimals(), 6, "Native USDC must use six decimals");
        assertFalse(usdc.paused(), "Pinned block must have operational USDC");

        ReserveConstitutionV0 constitution = new ReserveConstitutionV0();
        BaseForkLiabilityTokenMock liabilities = new BaseForkLiabilityTokenMock();
        ReserveVaultV0 vault = new ReserveVaultV0(BASE_USDC, address(constitution), address(liabilities));
        assertEq(liabilities.decimals(), 6);
        assertFalse(usdc.isBlacklisted(address(vault)), "Local vault must not be blacklisted");
        assertEq(usdc.balanceOf(address(vault)), 0);
        assertEq(vault.nominalReserveBalance(), 0);
        assertEq(vault.liquidReserveBalance(), 0);
        vault.validateReserveState();

        uint256 reserves = 100e6;
        // Foundry discovers the real token's storage layout. Packed-slot support preserves the
        // blacklist bit stored alongside balances in USDC implementations. No RPC writes occur.
        stdstore.target(BASE_USDC)
            .sig(usdc.balanceOf.selector)
            .with_key(address(vault))
            .enable_packed_slots()
            .checked_write(reserves);

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

        emit log_named_uint("USDC decimals", usdc.decimals());
        emit log_named_uint("Nominal reserve balance", vault.nominalReserveBalance());
        emit log_named_uint("Usable reserve balance", vault.liquidReserveBalance());
    }
}
