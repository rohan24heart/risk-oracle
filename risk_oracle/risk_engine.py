"""Deterministic risk scoring using supplied risk factors."""

from risk_oracle.providers.base import RiskInputs


def risk_level_for_score(score: float) -> str:
    if not 0 <= score <= 100:
        raise ValueError("Score must be between 0 and 100.")
    if score < 25:
        return "low"
    if score < 50:
        return "moderate"
    if score < 75:
        return "high"
    return "critical"


def calculate_risk(inputs: RiskInputs) -> dict[str, float | str]:
    score = (inputs.oracle_risk + inputs.liquidity_risk + inputs.liquidation_risk) / 3

    return {
        "score": score,
        "risk_level": risk_level_for_score(score),
        "confidence": 0.5,
    }
