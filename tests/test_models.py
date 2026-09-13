"""Synthetic fixtures only: no network, credentials, or live chain data."""
from datetime import datetime, timedelta, timezone
import json

import pytest
from pydantic import ValidationError

from risk_oracle.models import (
    BlockRef, EvidenceRecord, Freshness, FreshnessStatus, Observation,
    PrecomputedAssessment, ReserveSnapshot, shared_json_schema,
)

T = datetime(2026, 1, 1, tzinfo=timezone.utc)
H = "0x" + "11" * 32
A = "0x" + "22" * 20


def freshness(status="FRESH"):
    return dict(status=status, evaluated_at=T,
                fresh_until=None if status == "UNKNOWN" else T + timedelta(minutes=20),
                expires_at=None if status == "UNKNOWN" else T + timedelta(minutes=30),
                reasons=[] if status == "FRESH" else ["synthetic_reason"])


def block():
    return dict(chain_id=8453, number="123", hash=H, parent_hash="0x" + "00" * 32,
                timestamp=T, finality="safe", canonicality_checked_at=T)


def evidence():
    return dict(id="e1", content_hash=H, canonicalization_version="v1",
                source=dict(kind="onchain_rpc", provider_id="synthetic", block=block(),
                            contract=A, method="eth_call", call_signature="balanceOf(address)",
                            call_data="0x00", abi_hash=H, decoder_version="v1"),
                collected_at=T, source_updated_at=None, run_id="run1", code_revision="test",
                normalizer_version="v1", parent_evidence_ids=[], transformation=None,
                outcome="success", raw_result="0x00", error_code=None)


def observation(value=0):
    return dict(id="o1", field="cash", state="present", datatype="uint256", value=value,
                unit=dict(name="token_base_units", decimals=6, quote_currency=None),
                block=block(), collected_at=T, source_updated_at=None,
                freshness=freshness(), evidence_ids=["e1"], reason=None)


def subject():
    return dict(chain_id=8453, protocol="aave-v3", pool=A, asset=A)


def snapshot():
    return dict(id="s1", subject=subject(), block=block(), collected_at=T, run_id="run1",
                manifest_hash=H, acquisition_status="complete", observations={"cash": observation()},
                evidence=[evidence()], freshness=freshness())


def assessment():
    return dict(id="a1", subject=subject(), snapshot_id="s1", snapshot_hash=H,
                policy_hash=H, input_hash=H, computed_at=T, freshness=freshness(),
                score=50.0, risk_level="high", confidence=0.5, freshness_status="fresh", factors=[dict(
                    factor="synthetic", status="evaluated", score=50.0, explanation="Synthetic fixture",
                    rule_id="test-only", observation_ids=["o1"], evidence_ids=["e1"])],
                evidence_ids=["e1"], limitations=["synthetic fixture"])


def test_missing_is_not_zero():
    zero = Observation(**observation(0))
    missing = observation(None)
    missing.update(state="missing", reason="acquisition_failed", freshness=freshness("UNKNOWN"))
    absent = Observation(**missing)
    assert zero.value == 0 and absent.value is None
    assert json.loads(zero.model_dump_json())["value"] == "0"
    assert json.loads(absent.model_dump_json())["value"] is None
    assert absent.freshness.status_at(T + timedelta(days=5)) == FreshnessStatus.UNKNOWN


@pytest.mark.parametrize("state,value", [("present", None), ("missing", 0), ("invalid", 0)])
def test_state_value_mismatch_rejected(state, value):
    data = observation(value)
    data.update(state=state, reason="test")
    with pytest.raises(ValidationError):
        Observation(**data)


def test_uint256_round_trip_through_snapshot():
    data = snapshot()
    data["observations"]["cash"]["value"] = 2**256 - 1
    data["block"]["number"] = 2**256 - 1
    data["observations"]["cash"]["block"] = data["block"]
    data["evidence"][0]["source"]["block"] = data["block"]
    model = ReserveSnapshot(**data)
    wire = model.model_dump_json()
    assert json.loads(wire)["observations"]["cash"]["value"] == str(2**256 - 1)
    assert json.loads(wire)["block"]["number"] == str(2**256 - 1)
    restored = ReserveSnapshot.model_validate_json(wire)
    assert restored == model
    assert restored.observations["cash"].value == 2**256 - 1


@pytest.mark.parametrize("value", [-1, 2**256, True, 1.0, "01", "1e3", "-0"])
def test_invalid_uint256_rejected(value):
    with pytest.raises(ValidationError):
        Observation(**observation(value))


@pytest.mark.parametrize("location", ["observation", "evidence"])
@pytest.mark.parametrize("field,value", [("number", "124"), ("hash", "0x" + "33" * 32),
                                          ("chain_id", 1)])
def test_mixed_block_rejected(location, field, value):
    data = snapshot()
    target = (data["observations"]["cash"]["block"] if location == "observation"
              else data["evidence"][0]["source"]["block"])
    target[field] = value
    with pytest.raises(ValidationError):
        ReserveSnapshot(**data)


@pytest.mark.parametrize("minutes,status", [(0, "FRESH"), (19, "FRESH"), (20, "DEGRADED"),
                                            (29, "DEGRADED"), (30, "STALE"), (60, "STALE")])
def test_freshness_boundaries(minutes, status):
    assert Freshness(**freshness()).status_at(T + timedelta(minutes=minutes)) == status


def test_degraded_does_not_become_fresh_with_time():
    assert Freshness(**freshness("DEGRADED")).status_at(T) == "DEGRADED"


@pytest.mark.parametrize("change", [dict(fresh_until=T + timedelta(hours=1)),
    dict(expires_at=None), dict(evaluated_at=T + timedelta(minutes=30)),
    dict(status="STALE", reasons=["expired"]), dict(status="UNKNOWN", reasons=["missing"])])
def test_invalid_freshness_rejected(change):
    data = freshness()
    data.update(change)
    with pytest.raises(ValidationError):
        Freshness(**data)


def test_explicit_utc_timestamps_required():
    data = block()
    data["timestamp"] = datetime(2026, 1, 1)
    with pytest.raises(ValidationError):
        BlockRef(**data)
    with pytest.raises(ValueError):
        Freshness(**freshness()).status_at(T - timedelta(seconds=1))


@pytest.mark.parametrize("model,factory", [(Observation, observation), (EvidenceRecord, evidence),
    (BlockRef, block), (ReserveSnapshot, snapshot), (PrecomputedAssessment, assessment)])
def test_model_json_round_trip(model, factory):
    original = model(**factory())
    assert model.model_validate_json(original.model_dump_json()) == original
    assert model.model_json_schema(mode="serialization")["type"] == "object"


@pytest.mark.parametrize("field", ["source", "collected_at", "content_hash", "run_id", "code_revision"])
def test_required_evidence_provenance(field):
    data = evidence()
    del data[field]
    with pytest.raises(ValidationError):
        EvidenceRecord(**data)


@pytest.mark.parametrize("field", ["unit", "collected_at", "evidence_ids", "source_updated_at"])
def test_required_observation_metadata(field):
    data = observation()
    del data[field]
    with pytest.raises(ValidationError):
        Observation(**data)


@pytest.mark.parametrize("change", [dict(evidence_ids=[]), dict(evidence_ids=["absent"]), dict(block=None)])
def test_missing_provenance_rejected(change):
    data = snapshot()
    data["observations"]["cash"].update(change)
    with pytest.raises(ValidationError):
        ReserveSnapshot(**data)


def test_offchain_evidence_does_not_need_fake_block():
    data = snapshot()
    data["observations"]["cash"]["block"] = None
    data["evidence"][0]["source"] = dict(kind="service_policy", reference="urn:test:policy",
        section="cash", revision="v1", document_hash=H)
    assert ReserveSnapshot(**data).observations["cash"].block is None


def test_unknown_assessment_has_no_score():
    data = assessment()
    data.update(freshness=freshness("UNKNOWN"), score=None, risk_level="unknown", confidence=None, freshness_status="unknown")
    assert PrecomputedAssessment(**data).score is None
    data["score"] = 0
    with pytest.raises(ValidationError):
        PrecomputedAssessment(**data)


def test_stale_is_distinct_from_unknown():
    data = assessment()
    data["computed_at"] = T + timedelta(minutes=30)
    data["freshness"].update(evaluated_at=data["computed_at"], status="STALE", reasons=["expired"])
    data["freshness_status"] = "stale"
    model = PrecomputedAssessment(**data)
    assert model.score == 50.0
    assert model.freshness.status_at(T + timedelta(hours=1)) == "STALE"


@pytest.mark.parametrize("datatype,value", [("int256", -(2**255)), ("boolean", False), ("text", "0")])
def test_typed_values_round_trip(datatype, value):
    data = observation(value)
    data["datatype"] = datatype
    model = Observation(**data)
    restored = Observation.model_validate_json(model.model_dump_json())
    assert type(restored.value) is type(value)
    assert restored.value == value


def test_schema_bundle_contains_five_contracts():
    schema = json.loads(json.dumps(shared_json_schema()))
    for name in ("Observation", "EvidenceRecord", "BlockRef", "ReserveSnapshot", "PrecomputedAssessment"):
        assert name in schema["$defs"]
    assert schema["$defs"]["BlockRef"]["properties"]["number"]["type"] == "string"
    assert "evidence_ids" in schema["$defs"]["Observation"]["required"]


def test_cross_run_evidence_rejected():
    data = snapshot()
    data["evidence"][0]["run_id"] = "another_run"
    with pytest.raises(ValidationError):
        ReserveSnapshot(**data)


@pytest.mark.parametrize("field", ["provider_id", "abi_hash", "block", "call_signature"])
def test_rpc_source_fields_are_required(field):
    data = evidence()
    del data["source"][field]
    with pytest.raises(ValidationError):
        EvidenceRecord(**data)


def test_safe_provider_label_rejects_url():
    data = evidence()
    data["source"]["provider_id"] = "https://example.test/secret"
    with pytest.raises(ValidationError):
        EvidenceRecord(**data)


def test_unknown_requires_null_confidence():
    data = assessment()
    data.update(freshness=freshness("UNKNOWN"), score=None, risk_level="unknown", freshness_status="unknown")
    with pytest.raises(ValidationError):
        PrecomputedAssessment(**data)


def test_schema_artifact_matches_models():
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "docs" / "schemas" / "shared-models.schema.json"
    assert json.loads(path.read_text(encoding="utf-8")) == shared_json_schema()


@pytest.mark.parametrize("score,level", [(0.0, "low"), (24.99, "low"), (25.0, "moderate"),
    (49.99, "moderate"), (50.0, "high"), (74.99, "high"), (75.0, "critical"), (100.0, "critical")])
def test_canonical_score_boundaries(score, level):
    data = assessment()
    data.update(score=score, risk_level=level)
    model = PrecomputedAssessment(**data)
    assert model.score == score and model.risk_level == level


@pytest.mark.parametrize("field,value", [("score", -0.1), ("score", 100.1), ("score", float("nan")),
    ("score", float("inf")), ("score", True), ("confidence", -0.1), ("confidence", 1.1),
    ("confidence", float("nan")), ("confidence", float("inf")), ("confidence", True),
    ("risk_level", "highish"), ("risk_level", "HIGH"), ("freshness_status", "FRESH")])
def test_invalid_canonical_assessment_values(field, value):
    data = assessment()
    data[field] = value
    with pytest.raises(ValidationError):
        PrecomputedAssessment(**data)


@pytest.mark.parametrize("confidence", [None, 0.0, 1.0])
def test_confidence_nullable_and_boundaries(confidence):
    data = assessment()
    data["confidence"] = confidence
    assert PrecomputedAssessment(**data).confidence == confidence


def test_existing_mock_result_is_accepted_without_rescaling():
    from risk_oracle.providers.mock import MockProvider
    from risk_oracle.risk_engine import calculate_risk
    result = calculate_risk(MockProvider().get_inputs(chain="base", protocol="aave-v3", asset="USDC"))
    data = assessment()
    data.update(result)
    model = PrecomputedAssessment(**data)
    assert (model.score, model.risk_level, model.confidence) == (50.0, "high", 0.5)
    assert model.model_dump(mode="json")["freshness_status"] == "fresh"


def test_missing_required_factor_requires_unknown_null_assessment():
    data = assessment()
    data["factors"][0].update(status="unknown", score=None)
    with pytest.raises(ValidationError, match="Missing required factor"):
        PrecomputedAssessment(**data)
    data.update(score=None, risk_level="unknown", confidence=None, freshness_status="unknown",
                freshness=freshness("UNKNOWN"))
    model = PrecomputedAssessment(**data)
    assert model.score is None and model.confidence is None


def test_status_must_match_freshness_metadata():
    data = assessment()
    data["freshness_status"] = "degraded"
    with pytest.raises(ValidationError, match="freshness_status"):
        PrecomputedAssessment(**data)


def test_canonical_schema_uses_numeric_nullable_ranges():
    schema = PrecomputedAssessment.model_json_schema(mode="serialization")
    assert schema["properties"]["score"]["anyOf"][0]["maximum"] == 100
    assert schema["properties"]["confidence"]["anyOf"][0]["maximum"] == 1
    assert schema["properties"]["freshness_status"]["enum"] == ["fresh", "degraded", "stale", "unknown"]
    assert "level" not in schema["properties"]
