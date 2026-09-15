// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.13;

/// @notice Stateless validation of proposed reserves; all amounts must use the same unit.
contract ReserveConstitutionV0 {
    uint256 public constant BPS = 10_000;
    uint256 public constant MIN_LIQUID_BPS = 2_500;
    uint256 public constant MAX_AAVE_BPS = 4_000;

    error InsufficientBacking();
    error InsufficientLiquidUSDC();
    error ExcessiveAaveExposure();
    error UnrecognizedReserveAmount();

    /// @notice Returns normally for a valid state, otherwise reverts with the first violation.
    /// @dev Checks backing, reserve accounting, liquidity, then Aave exposure.
    ///      Zero reserves with zero liabilities are valid under the V0 rules.
    ///      All four amounts use the same accounting unit and decimal scale.
    ///      Liquid and Aave reserves are mutually exclusive: they must not overlap.
    ///      This validates supplied amounts, not their accuracy or actual balances.
    /// @param totalBacking Total reserves, exactly equal to liquidUSDC + aaveUSDC.
    /// @param totalLiabilities Outstanding stablecoin liabilities in the same accounting unit.
    /// @param liquidUSDC Idle/liquid USDC held outside Aave, in the common accounting unit.
    /// @param aaveUSDC Underlying USDC value allocated to Aave, in the common accounting unit.
    function validateReserveState(uint256 totalBacking, uint256 totalLiabilities, uint256 liquidUSDC, uint256 aaveUSDC)
        external
        pure
    {
        if (totalBacking < totalLiabilities) revert InsufficientBacking();
        // Subtraction avoids overflow when proposed reserve components are invalid.
        if (liquidUSDC > totalBacking || aaveUSDC != totalBacking - liquidUSDC) {
            revert UnrecognizedReserveAmount();
        }

        // Split the calculation to avoid overflow, rounding the minimum up and maximum down.
        uint256 minimumLiquid =
            (totalBacking / BPS) * MIN_LIQUID_BPS + ((totalBacking % BPS) * MIN_LIQUID_BPS + BPS - 1) / BPS;
        if (liquidUSDC < minimumLiquid) revert InsufficientLiquidUSDC();

        uint256 maximumAave = (totalBacking / BPS) * MAX_AAVE_BPS + ((totalBacking % BPS) * MAX_AAVE_BPS) / BPS;
        if (aaveUSDC > maximumAave) revert ExcessiveAaveExposure();
    }
}
