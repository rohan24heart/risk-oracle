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

/// @dev The configured token must be the Aave aToken for the reserve asset, not a scaled/static wrapper.
interface IAaveTokenV0 {
    function balanceOf(address account) external view returns (uint256);
    function decimals() external view returns (uint8);
    function UNDERLYING_ASSET_ADDRESS() external view returns (address);
}

/// @notice Validates usable idle reserves plus Aave exposure against the liability token's total supply.
/// @dev Fund the vault by transferring the configured token directly to its address.
///      Only usable idle reserves and the configured Aave position are recognized as backing.
contract ReserveVaultV0 {
    IReserveTokenV0 public immutable reserveToken;
    ReserveConstitutionV0 public immutable constitution;
    ILiabilityTokenV0 public immutable liabilityToken;
    IAaveTokenV0 public immutable aaveToken;

    error InvalidReserveToken();
    error InvalidConstitution();
    error InvalidLiabilityToken();
    error DecimalScaleMismatch();
    error InvalidAaveToken();
    error AaveUnderlyingMismatch();

    /// @dev Dependencies must be deployed contracts. Their correctness is trusted.
    ///      Reserve, liability and aToken decimal scales must match; no conversion is performed.
    ///      The liability token's entire supply is treated as outstanding reserve-backed liabilities.
    constructor(
        address reserveTokenAddress,
        address constitutionAddress,
        address liabilityTokenAddress,
        address aaveTokenAddress
    ) {
        if (reserveTokenAddress.code.length == 0) revert InvalidReserveToken();
        if (constitutionAddress.code.length == 0) revert InvalidConstitution();
        if (liabilityTokenAddress.code.length == 0) revert InvalidLiabilityToken();
        if (aaveTokenAddress.code.length == 0 || aaveTokenAddress == reserveTokenAddress) revert InvalidAaveToken();
        reserveToken = IReserveTokenV0(reserveTokenAddress);
        constitution = ReserveConstitutionV0(constitutionAddress);
        liabilityToken = ILiabilityTokenV0(liabilityTokenAddress);
        aaveToken = IAaveTokenV0(aaveTokenAddress);
        uint8 reserveDecimals = reserveToken.decimals();
        if (reserveDecimals != liabilityToken.decimals() || reserveDecimals != aaveToken.decimals()) {
            revert DecimalScaleMismatch();
        }
        if (aaveToken.UNDERLYING_ASSET_ADDRESS() != reserveTokenAddress) revert AaveUnderlyingMismatch();
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

    /// @notice Aave position in underlying reserve units, including accrued interest.
    /// @dev Uses balanceOf, not scaledBalanceOf. This is exposure, not a promise of immediate withdrawal.
    function aaveReserveBalance() public view returns (uint256) {
        return aaveToken.balanceOf(address(this));
    }

    /// @notice Recognized backing: usable idle reserves plus the configured Aave position.
    function totalBacking() public view returns (uint256) {
        return liquidReserveBalance() + aaveReserveBalance();
    }

    /// @notice Reverts with a constitutional error if the internally derived reserve state is invalid.
    /// @dev Reads current total supply on every call, in the shared token decimal scale.
    function validateReserveState() external view {
        uint256 liquidUSDC = liquidReserveBalance();
        uint256 totalLiabilities = liabilityToken.totalSupply();
        uint256 aaveUSDC = aaveReserveBalance();
        uint256 backing = liquidUSDC + aaveUSDC;
        constitution.validateReserveState(backing, totalLiabilities, liquidUSDC, aaveUSDC);
    }
}
