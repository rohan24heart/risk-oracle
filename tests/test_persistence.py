"""Offline persistence tests; all settings and HTTP requests are synthetic."""
import json
from uuid import UUID

import httpx
import pytest

from risk_oracle import persistence
from risk_oracle.config import Settings
from risk_oracle.models import EvidenceRecord, PrecomputedAssessment, ReserveSnapshot
from test_models import assessment as assessment_data, snapshot as snapshot_data


@pytest.fixture
def models():
    return PrecomputedAssessment(**assessment_data()), ReserveSnapshot(**snapshot_data())


@pytest.fixture
def http(monkeypatch):
    state = dict(calls=[], fail_at=None, status=403, secret="sb_secret_offline_test_credential")
    monkeypatch.setattr(persistence, "load_settings", lambda: Settings(
        supabase_url="https://private-project.example.test", supabase_service_key=state["secret"]))

    def post(url, *, headers, json, timeout, follow_redirects):
        state["calls"].append((url, headers, json))
        assert timeout == 15.0 and follow_redirects is False
        if len(state["calls"]) == state["fail_at"]:
            if state.get("transport_error"):
                raise httpx.ReadTimeout("https://private-project.example.test " + state["secret"])
            return httpx.Response(state["status"], text=url + state["secret"])
        return httpx.Response(201)

    monkeypatch.setattr(persistence.httpx, "post", post)
    return state


def write(models):
    return persistence.SupabaseWriter().write(*models, methodology_version="v0-test")


def test_writes_three_tables_with_complete_model_payloads(models, http):
    result = write(models)
    assessment, snapshot = models
    assert [call[0].rsplit("/", 1)[1] for call in http["calls"]] == [
        "assessments", "reserve_snapshots", "evidence_records"]
    row = http["calls"][0][2][0]
    assert UUID(row["id"]) == result.assessment_id
    assert row["score"] == 50.0 and row["risk_level"] == "high"
    assert row["calculated_at"] == assessment.model_dump(mode="json")["computed_at"]
    assert row["stale_after"] == assessment.model_dump(mode="json")["freshness"]["expires_at"]
    assert row["confidence"] == 0.5
    assert row["freshness_status"] == "fresh"
    stored_snapshot = http["calls"][1][2][0]
    assert stored_snapshot["snapshot_json"] == snapshot.model_dump(mode="json")
    assert stored_snapshot["assessment_id"] == row["id"]
    evidence_rows = http["calls"][2][2]
    assert EvidenceRecord.model_validate(evidence_rows[0]["evidence_json"]) == snapshot.evidence[0]
    full_assessment = evidence_rows[-1]["evidence_json"]["raw_result"]
    assert PrecomputedAssessment.model_validate_json(full_assessment) == assessment
    assert all(item["assessment_id"] == row["id"] for item in evidence_rows)
    assert evidence_rows[0]["source_url"] is None
    assert "Authorization" not in http["calls"][0][1]


def test_preserves_supplied_uuids(models, http):
    assessment, snapshot = models
    data = snapshot.model_dump()
    data["id"] = "00000000-0000-4000-8000-000000000001"
    snapshot = ReserveSnapshot(**data)
    data = assessment.model_dump()
    data.update(id="00000000-0000-4000-8000-000000000002", snapshot_id=snapshot.id)
    assessment = PrecomputedAssessment(**data)
    result = write((assessment, snapshot))
    assert str(result.assessment_id) == assessment.id
    assert str(result.snapshot_id) == snapshot.id


def test_content_ids_map_to_stable_uuids(models, http):
    first = write(models)
    second = write(models)
    assert first == second


def test_exact_uint256_json_round_trip(models, http):
    assessment, snapshot = models
    data = snapshot.model_dump()
    maximum = 2**256 - 1
    data["block"]["number"] = maximum
    data["observations"]["cash"]["value"] = maximum
    data["observations"]["cash"]["block"]["number"] = maximum
    data["evidence"][0]["source"]["block"]["number"] = maximum
    snapshot = ReserveSnapshot(**data)
    write((assessment, snapshot))
    assert http["calls"][0][2][0]["block_number"] == str(maximum)
    row = http["calls"][1][2][0]
    restored = ReserveSnapshot.model_validate_json(json.dumps(row["snapshot_json"]))
    assert restored.observations["cash"].value == maximum
    assert row["snapshot_json"]["observations"]["cash"]["value"] == str(maximum)


def test_unknown_preserves_null_score_and_expiry(models, http):
    assessment, snapshot = models
    data = assessment.model_dump()
    data.update(score=None, risk_level="unknown", confidence=None, freshness_status="unknown")
    data["freshness"].update(status="UNKNOWN", fresh_until=None, expires_at=None, reasons=["missing"])
    write((PrecomputedAssessment(**data), snapshot))
    row = http["calls"][0][2][0]
    assert row["score"] is None and row["stale_after"] is None
    assert row["risk_level"] == row["freshness_status"] == "unknown"


@pytest.mark.parametrize("stage", [1, 2, 3])
def test_api_failure_reports_partial_progress_without_secrets(models, http, stage, caplog, capsys):
    http["fail_at"] = stage
    with pytest.raises(persistence.PersistenceError) as caught:
        write(models)
    error = caught.value
    assert error.completed_tables == ("assessments", "reserve_snapshots", "evidence_records")[:stage - 1]
    assert error.table == ("assessments", "reserve_snapshots", "evidence_records")[stage - 1]
    assert "HTTP 403" in str(error)
    assert len(http["calls"]) == stage
    output = str(error) + caplog.text + capsys.readouterr().out
    assert http["secret"] not in output and "private-project.example.test" not in output


def test_timeout_has_unknown_commit_outcome(models, http):
    http.update(fail_at=2, transport_error=True)
    with pytest.raises(persistence.PersistenceError) as caught:
        write(models)
    assert caught.value.outcome_unknown is True
    assert caught.value.completed_tables == ("assessments",)
    assert "private-project" not in str(caught.value)
    assert http["secret"] not in str(caught.value)


def test_identity_mismatch_fails_before_writing(models, http):
    assessment, snapshot = models
    data = assessment.model_dump()
    data["snapshot_id"] = "another_snapshot"
    with pytest.raises(persistence.PersistenceError, match="identity"):
        write((PrecomputedAssessment(**data), snapshot))
    assert http["calls"] == []


def test_missing_settings_fails_offline(models, http, monkeypatch):
    monkeypatch.setattr(persistence, "load_settings", lambda: Settings())
    with pytest.raises(persistence.PersistenceError, match="must be configured"):
        write(models)
    assert not http["calls"]


def test_legacy_service_key_uses_authorization_header(models, http):
    http["secret"] = "offline.synthetic.jwt"
    write(models)
    assert http["calls"][0][1]["Authorization"] == "Bearer offline.synthetic.jwt"


def test_credential_bearing_payload_is_rejected(models, http):
    assessment, snapshot = models
    data = assessment.model_dump()
    data["limitations"] = [http["secret"]]
    with pytest.raises(persistence.PersistenceError, match="configured credential"):
        write((PrecomputedAssessment(**data), snapshot))
    assert not http["calls"]


def test_writer_preserves_fresh_unscored_assessment(models, http):
    from risk_oracle.freshness_policy import snapshot_freshness
    assessment, snapshot = models
    data = assessment.model_dump()
    data.update(score=None, risk_level="unknown", confidence=None, freshness_status="fresh",
                freshness=snapshot_freshness(observed_at=snapshot.collected_at,
                                             calculated_at=assessment.computed_at))
    write((PrecomputedAssessment(**data), snapshot))
    row = http["calls"][0][2][0]
    assert row["score"] is None and row["confidence"] is None
    assert row["risk_level"] == "unknown" and row["freshness_status"] == "fresh"
    assert row["stale_after"] is not None


def test_explicit_assessment_methodology_cannot_be_mislabelled(models, http):
    assessment, snapshot = models
    assessment = PrecomputedAssessment(**{**assessment.model_dump(), "methodology_version": "v0.1"})
    with pytest.raises(persistence.PersistenceError, match="methodology version"):
        persistence.SupabaseWriter().write(assessment, snapshot, methodology_version="old-mock")
    assert http["calls"] == []
    persistence.SupabaseWriter().write(assessment, snapshot, methodology_version="v0.1")
    assert http["calls"][0][2][0]["methodology_version"] == "v0.1"
