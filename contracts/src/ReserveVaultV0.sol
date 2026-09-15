// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.13;

import {ReserveConstitutionV0} from "./ReserveConstitutionV0.sol";

/// @dev Requires USDC-style status getters; generic ERC-20 tokens may not support these.
interface IReserveTokenStatusV0 {
    function paused() external view returns (bool);
    function isBlacklisted(address account) external view returns (bool);
}

interface IReserveTokenV0 is IReserveTokenStatusV0 {
    function balanceOf(address account) external view returns (uint256);
    function decimals() external view returns (uint8);
}

interface ILiabilityTokenV0 {
    function totalSupply() external view returns (uint256);
    function decimals() external view returns (uint8);
}

/// @notice Validates usable reserves against the configured liability token's total supply.
/// @dev Fund the vault by transferring the configured token directly to its address.
///      V0 recognizes only usable liquid reserves as backing; Aave exposure is always zero.
contract ReserveVaultV0 {
    IReserveTokenV0 public immutable reserveToken;
    ReserveConstitutionV0 public immutable constitution;
    ILiabilityTokenV0 public immutable liabilityToken;

    error InvalidReserveToken();
    error InvalidConstitution();
    error InvalidLiabilityToken();
    error DecimalScaleMismatch();

    /// @dev Dependencies must be deployed contracts. Their correctness is trusted.
    ///      Both tokens must expose decimals() and use the same scale; no conversion is performed.
    ///      The liability token's entire supply is treated as outstanding reserve-backed liabilities.
    constructor(address reserveTokenAddress, address constitutionAddress, address liabilityTokenAddress) {
        if (reserveTokenAddress.code.length == 0) revert InvalidReserveToken();
        if (constitutionAddress.code.length == 0) revert InvalidConstitution();
        if (liabilityTokenAddress.code.length == 0) revert InvalidLiabilityToken();
        reserveToken = IReserveTokenV0(reserveTokenAddress);
        constitution = ReserveConstitutionV0(constitutionAddress);
        liabilityToken = ILiabilityTokenV0(liabilityTokenAddress);
        if (reserveToken.decimals() != liabilityToken.decimals()) revert DecimalScaleMismatch();
    }

    /// @notice Nominal holdings in the token's smallest units, regardless of pause/blacklist status.
    function nominalReserveBalance() public view returns (uint256) {
        return reserveToken.balanceOf(address(this));
    }

    /// @notice Nominal holdings if unpaused and this vault is not blacklisted; otherwise zero.
    /// @dev Status-read failures revert. These checks cover USDC-style token controls only,
    ///      not restrictions on a particular transfer recipient or other token-specific controls.
    function liquidReserveBalance() public view returns (uint256) {
        if (reserveToken.paused() || reserveToken.isBlacklisted(address(this))) return 0;
        return nominalReserveBalance();
    }

    /// @notice Reverts with a constitutional error if observed reserves cannot support liabilities.
    /// @dev Reads current total supply on every call, in the shared token decimal scale.
    function validateReserveState() external view {
        uint256 liquidUSDC = liquidReserveBalance();
        uint256 totalLiabilities = liabilityToken.totalSupply();
        uint256 totalBacking = liquidUSDC; // Only liquid reserves are recognized in this vault version.
        constitution.validateReserveState(totalBacking, totalLiabilities, liquidUSDC, 0);
    }
}
