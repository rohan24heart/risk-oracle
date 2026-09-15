// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.13;

import {Test} from "forge-std/Test.sol";
import {ReserveConstitutionV0} from "../src/ReserveConstitutionV0.sol";

contract ReserveConstitutionV0Test is Test {
    ReserveConstitutionV0 public constitution;

    function setUp() public {
        constitution = new ReserveConstitutionV0();
    }

    function test_ValidReserveState() public view {
        constitution.validateReserveState(100, 90, 70, 30);
    }

    function test_InsufficientBacking() public {
        vm.expectRevert(ReserveConstitutionV0.InsufficientBacking.selector);
        constitution.validateReserveState(100, 101, 70, 30);
    }

    function test_TooLittleLiquidUSDC() public {
        // With only two reserve locations, this necessarily also exceeds the Aave cap.
        vm.expectRevert(ReserveConstitutionV0.InsufficientLiquidUSDC.selector);
        constitution.validateReserveState(100, 100, 24, 76);
    }

    function test_ExcessiveAaveExposure() public {
        vm.expectRevert(ReserveConstitutionV0.ExcessiveAaveExposure.selector);
        constitution.validateReserveState(100, 100, 59, 41);
    }

    function test_UnrecognizedReserveAmount() public {
        vm.expectRevert(ReserveConstitutionV0.UnrecognizedReserveAmount.selector);
        constitution.validateReserveState(100, 100, 60, 30);
    }

    function test_Exactly25PercentLiquidPassesLiquidityButFailsAaveCap() public {
        // The liquid boundary is inclusive, but the remaining 75% cannot all be in Aave.
        vm.expectRevert(ReserveConstitutionV0.ExcessiveAaveExposure.selector);
        constitution.validateReserveState(100, 100, 25, 75);
    }

    function test_Exactly40PercentAaveAndFullyBacked() public view {
        constitution.validateReserveState(100, 100, 60, 40);
    }

    function test_ZeroReservesAndLiabilities() public view {
        constitution.validateReserveState(0, 0, 0, 0);
    }

    function test_FractionalLiquidMinimumRoundsUp() public {
        vm.expectRevert(ReserveConstitutionV0.InsufficientLiquidUSDC.selector);
        constitution.validateReserveState(101, 100, 25, 76);
    }

    function test_FractionalAaveMaximumRoundsDown() public {
        constitution.validateReserveState(101, 100, 61, 40);
        vm.expectRevert(ReserveConstitutionV0.ExcessiveAaveExposure.selector);
        constitution.validateReserveState(101, 100, 60, 41);
    }

    function test_MaximumBackingDoesNotOverflow() public view {
        constitution.validateReserveState(type(uint256).max, type(uint256).max, type(uint256).max, 0);
    }

    function test_OverflowingReserveSumUsesCustomError() public {
        vm.expectRevert(ReserveConstitutionV0.UnrecognizedReserveAmount.selector);
        constitution.validateReserveState(type(uint256).max, 0, type(uint256).max, 1);
    }

    function test_LiquidAboveBackingUsesCustomError() public {
        vm.expectRevert(ReserveConstitutionV0.UnrecognizedReserveAmount.selector);
        constitution.validateReserveState(100, 100, 101, 0);
    }

    function testFuzz_ArbitraryStatesMatchRules(uint256 backing, uint256 liabilities, uint256 liquid, uint256 aave)
        public
        view
    {
        _assertMatchesRules(backing, liabilities, liquid, aave);
    }

    function testFuzz_AccountedStatesMatchRules(uint256 backing, uint256 liabilities, uint256 aave) public view {
        liabilities = bound(liabilities, 0, backing);
        aave = bound(aave, 0, backing);
        _assertMatchesRules(backing, liabilities, backing - aave, aave);
    }

    function testFuzz_ValidStatesAreAccepted(uint256 backing, uint256 liabilities, uint256 aave) public view {
        liabilities = bound(liabilities, 0, backing);
        aave = bound(aave, 0, _maximumAave(backing));
        // Every run exercises success, preventing vacuous accepted-state properties.
        constitution.validateReserveState(backing, liabilities, backing - aave, aave);
        _assertMatchesRules(backing, liabilities, backing - aave, aave);
    }

    function testFuzz_ExactPercentageBoundariesAboveBPS(uint256 scale) public view {
        // Multiples of 20 have integral 25% and 40% boundaries, without overflowing.
        scale = bound(scale, 501, type(uint256).max / 20);
        uint256 backing = scale * 20;
        uint256 liquidMinimum = scale * 5;
        uint256 aaveMaximum = scale * 8;
        _assertMatchesRules(backing, backing, liquidMinimum - 1, backing - liquidMinimum + 1);
        _assertMatchesRules(backing, backing, liquidMinimum, backing - liquidMinimum);
        _assertMatchesRules(backing, backing, backing - aaveMaximum, aaveMaximum);
        _assertMatchesRules(backing, backing, backing - aaveMaximum - 1, aaveMaximum + 1);
    }

    function test_TinyBackingRounding() public view {
        // Exhaust every accounted allocation and liability through one unit above backing.
        for (uint256 backing = 1; backing <= 9; backing++) {
            for (uint256 liquid = 0; liquid <= backing; liquid++) {
                for (uint256 liabilities = 0; liabilities <= backing + 1; liabilities++) {
                    _assertMatchesRules(backing, liabilities, liquid, backing - liquid);
                }
            }
        }
    }

    function test_ZeroBackingCombinations() public view {
        // Includes empty reserves, debt without backing, and either/both nonzero components.
        for (uint256 liabilities = 0; liabilities <= 1; liabilities++) {
            for (uint256 liquid = 0; liquid <= 1; liquid++) {
                for (uint256 aave = 0; aave <= 1; aave++) {
                    _assertMatchesRules(0, liabilities, liquid, aave);
                }
            }
        }
    }

    function test_NearUint256MaximumBoundaries() public view {
        // Consecutive values cover all remainders for the reduced denominators 4 and 5.
        for (uint256 offset = 0; offset < 20; offset++) {
            uint256 backing = type(uint256).max - offset;
            uint256 liquidMinimum = _minimumLiquid(backing);
            uint256 aaveMaximum = _maximumAave(backing);
            _assertMatchesRules(backing, backing, backing - aaveMaximum, aaveMaximum);
            _assertMatchesRules(backing, backing, backing - aaveMaximum - 1, aaveMaximum + 1);
            _assertMatchesRules(backing, backing, liquidMinimum, backing - liquidMinimum);
            _assertMatchesRules(backing, backing, liquidMinimum - 1, backing - liquidMinimum + 1);
            _assertMatchesRules(backing, 0, type(uint256).max, type(uint256).max);
        }
    }

    function _assertMatchesRules(uint256 backing, uint256 liabilities, uint256 liquid, uint256 aave) internal view {
        bytes4 expectedError;
        if (backing < liabilities) {
            expectedError = ReserveConstitutionV0.InsufficientBacking.selector;
        } else if (aave > backing || liquid != backing - aave) {
            expectedError = ReserveConstitutionV0.UnrecognizedReserveAmount.selector;
        } else if (liquid < _minimumLiquid(backing)) {
            expectedError = ReserveConstitutionV0.InsufficientLiquidUSDC.selector;
        } else if (aave > _maximumAave(backing)) {
            expectedError = ReserveConstitutionV0.ExcessiveAaveExposure.selector;
        }

        (bool accepted, bytes memory result) = address(constitution)
            .staticcall(abi.encodeCall(constitution.validateReserveState, (backing, liabilities, liquid, aave)));
        assertEq(accepted, expectedError == bytes4(0), "Acceptance must match the four rules");
        if (accepted) {
            assertGe(backing, liabilities, "Accepted state must cover liabilities");
            assertLe(aave, type(uint256).max - liquid, "Accepted reserve sum must not overflow");
            assertEq(liquid + aave, backing, "Accepted reserves must exactly equal backing");
            assertGe(liquid, _minimumLiquid(backing), "Accepted liquidity must be at least 25%");
            assertLe(aave, _maximumAave(backing), "Accepted Aave exposure must be at most 40%");
            assertEq(result.length, 0, "Successful validation returns no data");
        } else {
            // Exact bytes also reject arithmetic panics and incorrect error precedence.
            assertEq(result, abi.encodeWithSelector(expectedError), "Expected constitutional error");
        }
    }

    // Independent reference: reduced fractions, without using the production BPS constants.
    function _minimumLiquid(uint256 backing) internal pure returns (uint256) {
        return backing / 4 + (backing % 4 == 0 ? 0 : 1);
    }

    function _maximumAave(uint256 backing) internal pure returns (uint256) {
        return (backing / 5) * 2 + ((backing % 5) * 2) / 5;
    }
}
