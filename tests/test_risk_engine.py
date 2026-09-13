import pytest

from risk_oracle.providers.base import RiskInputs
from risk_oracle.risk_engine import calculate_risk, risk_level_for_score


@pytest.mark.parametrize(
    ("factors", "score", "risk_level"),
    [
        ((40, 60, 50), 50, "high"),
        ((10, 20, 30), 20, "low"),
        ((25, 40, 55), 40, "moderate"),
        ((70, 80, 90), 80, "critical"),
        ((0, 0, 1), 1 / 3, "low"),
    ],
)
def test_calculate_risk(
    factors: tuple[float, float, float], score: float, risk_level: str
) -> None:
    result = calculate_risk(
        RiskInputs(
            oracle_risk=factors[0],
            liquidity_risk=factors[1],
            liquidation_risk=factors[2],
        )
    )

    assert result == {
        "score": score,
        "risk_level": risk_level,
        "confidence": 0.5,
    }


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0, "low"),
        (24, "low"),
        (24.5, "low"),
        (25, "moderate"),
        (49, "moderate"),
        (49.5, "moderate"),
        (50, "high"),
        (74, "high"),
        (74.5, "high"),
        (75, "critical"),
        (100, "critical"),
    ],
)
def test_risk_level_mapping(score: float, expected: str) -> None:
    assert risk_level_for_score(score) == expected


@pytest.mark.parametrize("score", [-1, 101])
def test_risk_level_rejects_out_of_range_scores(score: float) -> None:
    with pytest.raises(ValueError, match="between 0 and 100"):
        risk_level_for_score(score)
