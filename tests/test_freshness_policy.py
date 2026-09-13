"""Offline boundary and unscored-assessment tests; no scorer or network calls."""
from datetime import timedelta

import pytest

from risk_oracle.freshness_policy import apply_snapshot_freshness, snapshot_freshness
from risk_oracle.models import PrecomputedAssessment, ReserveSnapshot
from test_models import T, assessment, snapshot


@pytest.mark.parametrize("minutes,micros,status", [(0, 0, "FRESH"), (25, 0, "FRESH"),
    (25, 1, "DEGRADED"), (45, 0, "DEGRADED"), (45, 1, "STALE"),
    (90, 0, "STALE"), (90, 1, "UNKNOWN")])
def test_exact_age_boundaries(minutes, micros, status):
    at = T + timedelta(minutes=minutes, microseconds=micros)
    result = snapshot_freshness(observed_at=T, calculated_at=at)
    assert result.status == status
    initial = snapshot_freshness(observed_at=T, calculated_at=T)
    assert initial.status_at(at) == status


@pytest.mark.parametrize("observed,calculated", [(T, T - timedelta(seconds=1)), (T.replace(tzinfo=None), T)])
def test_invalid_times_rejected(observed, calculated):
    with pytest.raises(ValueError):
        snapshot_freshness(observed_at=observed, calculated_at=calculated)


@pytest.mark.parametrize("minutes,status", [(0, "fresh"), (30, "degraded"), (60, "stale"), (91, "unknown")])
def test_unscored_live_assessment_has_independent_age_status(minutes, status):
    at = T + timedelta(minutes=minutes)
    original = ReserveSnapshot(**snapshot())
    updated = apply_snapshot_freshness(original, calculated_at=at)
    assert updated.observations == original.observations
    assert updated.evidence == original.evidence
    data = assessment()
    data.update(computed_at=at, freshness=updated.freshness, freshness_status=status,
                score=None, risk_level="unknown", confidence=None)
    data["factors"][0].update(status="unknown", score=None)
    result = PrecomputedAssessment(**data)
    assert result.score is None and result.confidence is None
    assert result.risk_level == "unknown" and result.freshness_status == status
    assert PrecomputedAssessment.model_validate_json(result.model_dump_json()) == result
