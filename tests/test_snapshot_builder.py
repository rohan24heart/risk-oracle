"""Offline builder tests using synthetic contract data."""
from datetime import datetime, timedelta, timezone
import json

import pytest
from pydantic import ValidationError

from risk_oracle.models import (
    BlockRef, EvidenceRecord, Freshness, Observation, ReserveSnapshot, ReserveSubject,
)
from risk_oracle.snapshot_builder import build_reserve_snapshot


@pytest.fixture
def inputs():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    digest = "0x" + "11" * 32
    address = "0x" + "22" * 20
    block = BlockRef(chain_id=8453, number=123, hash=digest, parent_hash="0x" + "00" * 32,
                     timestamp=now, finality="safe", canonicality_checked_at=now)
    freshness = Freshness(status="FRESH", evaluated_at=now,
                          fresh_until=now + timedelta(minutes=20),
                          expires_at=now + timedelta(minutes=30), reasons=())
    evidence = EvidenceRecord(
        id="e1", content_hash=digest, canonicalization_version="v1",
        source=dict(kind="onchain_rpc", provider_id="synthetic", block=block,
                    contract=address, method="eth_call", call_signature="synthetic()",
                    call_data="0x00", abi_hash=digest, decoder_version="v1"),
        collected_at=now, source_updated_at=None, run_id="run1", code_revision="synthetic",
        normalizer_version="v1", parent_evidence_ids=(), transformation=None,
        outcome="success", raw_result="0x00", error_code=None,
    )
    observation = Observation(
        id="o1", field="cash", state="present", datatype="uint256", value=0,
        unit=dict(name="token_base_units", decimals=6, quote_currency=None),
        block=block, collected_at=now, source_updated_at=None, freshness=freshness,
        evidence_ids=("e1",), reason=None,
    )
    return dict(snapshot_id="s1", subject=ReserveSubject(chain_id=8453, protocol="aave-v3",
                pool=address, asset=address), block=block, collected_at=now, run_id="run1",
                manifest_hash=digest, observations={"cash": observation}, evidence=[evidence],
                critical_fields={"cash"}, freshness=freshness)


@pytest.mark.parametrize("value", [0, 2**256 - 1])
def test_preserves_exact_values_and_provenance(inputs, value):
    original = inputs["observations"]["cash"].model_dump()
    original["value"] = value
    observation = Observation(**original)
    inputs["observations"]["cash"] = observation
    snapshot = build_reserve_snapshot(**inputs)
    assert snapshot.observations["cash"] == observation
    assert snapshot.evidence == tuple(inputs["evidence"])
    assert snapshot.block == inputs["block"]
    assert snapshot.freshness.status == "FRESH"
    assert snapshot.acquisition_status == "complete"
    wire = snapshot.model_dump_json()
    assert json.loads(wire)["observations"]["cash"]["value"] == str(value)
    assert ReserveSnapshot.model_validate_json(wire).observations["cash"].value == value


@pytest.mark.parametrize("omitted", [False, True])
def test_missing_critical_input_returns_unknown(inputs, omitted):
    if omitted:
        inputs["critical_fields"] = {"cash", "debt"}
        missing_field = "debt"
    else:
        missing_field = "cash"
        data = inputs["observations"]["cash"].model_dump()
        data.update(value=None, state="missing", reason="synthetic_failure", freshness=dict(
            status="UNKNOWN", evaluated_at=inputs["collected_at"], fresh_until=None,
            expires_at=None, reasons=("synthetic_failure",)))
        inputs["observations"]["cash"] = Observation(**data)
    snapshot = build_reserve_snapshot(**inputs)
    assert snapshot.freshness.status == "UNKNOWN"
    assert snapshot.freshness.fresh_until is None and snapshot.freshness.expires_at is None
    assert f"critical_input_missing:{missing_field}" in snapshot.freshness.reasons
    assert snapshot.acquisition_status != "complete"
    assert snapshot.observations == inputs["observations"]


@pytest.mark.parametrize("target", ["observation", "evidence"])
def test_mixed_blocks_rejected(inputs, target):
    if target == "observation":
        data = inputs["observations"]["cash"].model_dump()
        data["block"]["number"] += 1
        inputs["observations"]["cash"] = Observation(**data)
    else:
        data = inputs["evidence"][0].model_dump()
        data["source"]["block"]["hash"] = "0x" + "33" * 32
        inputs["evidence"] = [EvidenceRecord(**data)]
    with pytest.raises(ValidationError, match="Mixed-block"):
        build_reserve_snapshot(**inputs)


def test_missing_evidence_rejected(inputs):
    inputs["evidence"] = []
    with pytest.raises(ValidationError):
        build_reserve_snapshot(**inputs)


@pytest.mark.parametrize("minutes,status", [(20, "DEGRADED"), (30, "STALE")])
def test_input_deadlines_cannot_be_extended(inputs, minutes, status):
    now = inputs["collected_at"]
    inputs["freshness"] = Freshness(
        status="FRESH", evaluated_at=now + timedelta(minutes=minutes),
        fresh_until=now + timedelta(hours=1), expires_at=now + timedelta(hours=2), reasons=(),
    )
    snapshot = build_reserve_snapshot(**inputs)
    assert snapshot.freshness.status == status
    assert snapshot.freshness.expires_at == now + timedelta(minutes=30)
