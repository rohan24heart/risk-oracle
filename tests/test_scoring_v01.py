"""Offline v0.1 scoring tests using the existing fully mocked collector fixture."""
from datetime import timedelta
from fractions import Fraction
import json

import pytest
from pydantic import ValidationError

from test_aave_v3_base_collector import rpc, collect, verified_metadata, NOW
from risk_oracle.models import PrecomputedAssessment, ReserveSnapshot
from risk_oracle.scoring_v01 import assess_snapshot, calculate_confidence, FACTOR_NAMES, _rounded, _level


@pytest.fixture
def snapshot(rpc):
    verified_metadata(rpc)
    return collect()


def changed(snapshot, **values):
    data = snapshot.model_dump()
    for key, value in values.items():
        if value is None:
            del data["observations"][key]
        else:
            data["observations"][key]["value"] = value
    return ReserveSnapshot.model_validate(data)


def assess(snapshot, minutes=0):
    return assess_snapshot(snapshot, calculated_at=NOW + timedelta(minutes=minutes))


def factor(snapshot, index):
    return assess(snapshot).factors[index]


@pytest.mark.parametrize("age,expected", [(0, 0), (86400, 0), (86401, 100/86400),
                                           (129600, 50), (172800, 100), (259200, 100)])
def test_oracle_heartbeat_boundaries(snapshot, age, expected):
    snapshot = changed(snapshot, oracle_feed_updated_at=int(snapshot.block.timestamp.timestamp()) - age)
    assert factor(snapshot, 0).score == pytest.approx(expected)


@pytest.mark.parametrize("field,value,expected", [
    ("oracle_feed_latest_answer", 0, 100), ("oracle_feed_latest_answer", -1, 100),
    ("oracle_source_latest_answer", 0, 100), ("oracle_source_latest_answer", 123, 100),
    ("oracle_feed_updated_at", 0, None), ("oracle_feed_updated_at", int(NOW.timestamp()) + 1, None),
    ("oracle_feed_heartbeat", 0, None), ("oracle_feed_heartbeat", None, None),
    ("oracle_source_interface", "unverified", None), ("oracle_feed_is_chainlink", False, None),
])
def test_oracle_integrity_and_unknown(snapshot, field, value, expected):
    assert factor(changed(snapshot, **{field: value}), 0).score == expected


def test_correct_cap_is_not_oracle_failure_or_peg_accuracy_test(snapshot):
    snapshot = changed(snapshot, oracle_feed_latest_answer=110000000, oracle_source_latest_answer=104000000)
    assert factor(snapshot, 0).score == 0
    snapshot = changed(snapshot, oracle_feed_latest_answer=80000000, oracle_source_latest_answer=80000000)
    assert factor(snapshot, 0).score == 0


def test_oracle_comparison_uses_exact_scales(snapshot):
    data = changed(snapshot, oracle_source_decimals=18, oracle_source_latest_answer=99990000*10**10,
                   oracle_source_price_cap=104000000*10**10).model_dump()
    for name in ("oracle_source_latest_answer", "oracle_source_price_cap"):
        data["observations"][name]["unit"]["decimals"] = 18
    assert factor(ReserveSnapshot(**data), 0).score == 0


@pytest.mark.parametrize("debt,cash,expected", [(0,100,0), (45,55,25), (90,10,50), (95,5,75), (100,0,100)])
def test_liquidity_kink_boundaries(snapshot, debt, cash, expected):
    snapshot = changed(snapshot, total_supplied=100*10**6, variable_debt=debt*10**6,
                       available_liquidity=cash*10**6, virtual_available_liquidity=cash*10**6)
    assert factor(snapshot, 1).score == expected


def test_actual_cash_pressure_cannot_be_hidden_by_virtual_balance(snapshot):
    snapshot = changed(snapshot, total_supplied=100, variable_debt=10, virtual_available_liquidity=90, available_liquidity=0)
    assert factor(snapshot, 1).score == 100


@pytest.mark.parametrize("changes", [dict(total_supplied=0), dict(variable_debt=0, virtual_available_liquidity=0),
                                    dict(optimal_utilization=0), dict(optimal_utilization=10**27),
                                    dict(available_liquidity=None)])
def test_undefined_liquidity_is_unknown(snapshot, changes):
    assert factor(changed(snapshot, **changes), 1).score is None


@pytest.mark.parametrize("ltv,lt,bonus,expected", [
    (7500,8000,10500,0), (0,0,0,0), (0,8000,10500,0), (8000,8000,10500,50),
    (8100,8000,10500,100), (7500,10001,10500,100), (7500,8000,10000,100),
    (7500,8000,12500,100), (7500,8000,12501,100),
])
def test_collateral_configuration_rules(snapshot, ltv, lt, bonus, expected):
    assert factor(changed(snapshot, ltv=ltv, liquidation_threshold=lt, liquidation_bonus=bonus), 2).score == expected


@pytest.mark.parametrize("changes,expected", [
    ({},0), (dict(reserve_paused=True),100), (dict(reserve_active=False),100),
    (dict(reserve_frozen=True),50), (dict(borrowing_enabled=False),25),
    (dict(supply_cap=100000000),25), (dict(borrow_cap=60000000),25),
    (dict(reserve_frozen=True, reserve_paused=True),100),
    (dict(supply_cap=0,borrow_cap=0,supply_cap_total_supplied=None),0),
    (dict(reserve_paused=True,borrow_cap=None),None),
])
def test_operational_restriction_severity(snapshot, changes, expected):
    assert factor(changed(snapshot, **changes), 3).score == expected


def test_weighted_example_confidence_version_and_provenance(snapshot, monkeypatch):
    from risk_oracle import risk_engine
    monkeypatch.setattr(risk_engine, "calculate_risk", lambda *args: pytest.fail("Mock scorer invoked"))
    snapshot = changed(snapshot, total_supplied=100000000*10**6, variable_debt=85000000*10**6,
                       available_liquidity=15000000*10**6, virtual_available_liquidity=15000000*10**6)
    result = assess(snapshot)
    assert [item.score for item in result.factors] == [0, pytest.approx(47.2222222222), 0, 0]
    assert result.score == 11.81 and result.risk_level == "low"
    assert result.confidence == pytest.approx(4/7)
    assert result.methodology_version == "v0.1"
    assert tuple(item.factor for item in result.factors) == FACTOR_NAMES
    records = {item.id for item in snapshot.evidence}
    observations = {item.id for item in snapshot.observations.values()}
    for item in result.factors:
        assert item.rule_id.startswith("v0.1:") and item.explanation
        assert item.evidence_ids and set(item.evidence_ids) <= records
        assert item.observation_ids and set(item.observation_ids) <= observations
    assert PrecomputedAssessment.model_validate_json(result.model_dump_json()) == result
    assert assess(snapshot) == result
    assert "methodology_version" in PrecomputedAssessment.model_json_schema()["properties"]


def test_critical_floor_and_full_scale(snapshot):
    result = assess(changed(snapshot, reserve_paused=True))
    assert result.score == 75 and result.risk_level == "critical"
    result = assess(changed(snapshot, reserve_paused=True, oracle_source_latest_answer=0,
                            available_liquidity=0, ltv=9000))
    assert result.score == 100


@pytest.mark.parametrize("field", ["oracle_feed_heartbeat", "available_liquidity", "ltv", "reserve_frozen"])
def test_unknown_propagation_no_reweighting(snapshot, field):
    result = assess(changed(snapshot, **{field: None}))
    assert result.score is None and result.confidence is None and result.risk_level == "unknown"
    assert sum(item.status == "unknown" for item in result.factors) == 1
    assert result.freshness_status == "fresh"


@pytest.mark.parametrize("status,expected", [("fresh",4/7),("degraded",3/7),("stale",0),("unknown",0)])
def test_confidence_is_separate_coverage(status, expected):
    assert calculate_confidence(factors_complete=True, freshness_status=status) == pytest.approx(expected)
    assert calculate_confidence(factors_complete=False, freshness_status=status) is None


@pytest.mark.parametrize("seconds,status,confidence", [(1490,"fresh",4/7),(1491,"degraded",3/7),
    (2690,"degraded",3/7),(2691,"stale",None),(5390,"stale",None),(5391,"unknown",None)])
def test_assessment_freshness_from_block_and_null_current_score(snapshot, seconds, status, confidence):
    result = assess_snapshot(snapshot, calculated_at=NOW+timedelta(seconds=seconds))
    assert result.freshness_status == status
    assert result.confidence == (pytest.approx(confidence) if confidence is not None else None)
    assert (result.score is None) == (confidence is None)
    if confidence is not None:
        assert result.score == assess(snapshot).score


def test_mixed_block_rejected_even_when_model_copy_bypasses_validation(snapshot):
    item = snapshot.observations["ltv"]
    bad = item.model_copy(update={"block": snapshot.block.model_copy(update={"number":124})})
    snapshot = snapshot.model_copy(update={"observations":{**snapshot.observations,"ltv":bad}})
    with pytest.raises(ValidationError, match="Mixed-block"):
        assess(snapshot)


def test_future_timestamps_rejected(snapshot):
    with pytest.raises(ValueError, match="Future"):
        assess_snapshot(snapshot, calculated_at=NOW-timedelta(seconds=1))


def test_old_block_is_not_freshened_by_new_acquisition(snapshot):
    old = snapshot.model_copy(update={"collected_at": NOW+timedelta(hours=2)})
    result = assess_snapshot(old, calculated_at=NOW+timedelta(hours=2))
    assert result.freshness_status == "unknown" and result.score is None


def test_wholly_absent_factor_has_no_fabricated_observation_reference(snapshot):
    result = assess(changed(snapshot, aave_price_oracle=None))
    assert result.factors[0].status == "unknown"
    assert result.factors[0].observation_ids == () and result.factors[0].evidence_ids == ()


def test_unit_mismatch_is_unknown(snapshot):
    data = snapshot.model_dump()
    data["observations"]["available_liquidity"]["unit"]["decimals"] = 18
    assert factor(ReserveSnapshot(**data),1).score is None


@pytest.mark.parametrize("raw,rounded,level", [("24.994",24.99,"low"),("24.995",25,"moderate"),
    ("49.995",50,"high"),("74.995",75,"critical")])
def test_half_up_rounding_and_thresholds(raw, rounded, level):
    assert _rounded(Fraction(raw)) == rounded
    assert _level(rounded) == level
