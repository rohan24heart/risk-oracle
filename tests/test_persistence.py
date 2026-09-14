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


@pytest.mark.parametrize("status,code,phrase", [
    (401, "PGRST301", "JWT verification"),
    (403, "42501", "permission denied"),
    (409, "23505", "duplicate record"),
    (400, "23514", "check constraint"),
    (400, "PGRST204", "schema cache"),
])
def test_structured_http_diagnostics_discard_sensitive_body(models, http, monkeypatch, status, code, phrase):
    def post(url, **kwargs):
        http["calls"].append((url, kwargs))
        return httpx.Response(status, json={
            "code": code, "message": "Authorization: Bearer " + http["secret"],
            "details": "https://user:password@private.test/path?api_key=" + http["secret"],
            "hint": "BASE_RPC_URL=https://rpc.test/private-token",})
    monkeypatch.setattr(persistence.httpx, "post", post)
    with pytest.raises(persistence.PersistenceError) as caught:
        write(models)
    error = caught.value
    assert error.http_status == status and error.error_code == code
    assert phrase in error.sanitized_message
    assert error.table == "assessments" and error.completed_tables == ()
    assert error.outcome_unknown is False and len(http["calls"]) == 1
    output = str(error) + json.dumps(vars(error))
    for forbidden in (http["secret"], "Authorization", "Bearer", "https://", "password", "private-token"):
        assert forbidden not in output


@pytest.mark.parametrize("body", ["<html>private-token</html>", "[]", '{"code":123}',
    '{"code":"sb_secret_do_not_log","message":"private-token"}',
    '{"code":"ZZ999","message":"private-token"}'])
def test_nonstandard_error_bodies_remain_safe(models, http, monkeypatch, body):
    monkeypatch.setattr(persistence.httpx, "post", lambda *args, **kwargs: httpx.Response(502, text=body))
    with pytest.raises(persistence.PersistenceError) as caught:
        write(models)
    error = caught.value
    assert error.http_status == 502 and error.table == "assessments"
    assert error.error_code == ("ZZ999" if "ZZ999" in body else None)
    assert "private-token" not in str(error) and "sb_secret" not in str(error)


def test_local_failure_has_safe_message_and_no_http_metadata(models, http, monkeypatch):
    monkeypatch.setattr(persistence, "load_settings", lambda: Settings(
        supabase_url="https://user:private-password@example.test", supabase_service_key=http["secret"]))
    with pytest.raises(persistence.PersistenceError) as caught:
        write(models)
    assert caught.value.sanitized_message == "Invalid Supabase project URL configuration."
    assert caught.value.http_status is None and caught.value.error_code is None and caught.value.table is None
    assert http["calls"] == []

@pytest.mark.parametrize('failure', [408, 503, 504, 'transport'])
def test_transient_first_insert_then_success(models, http, monkeypatch, failure):
    calls, delays = [], []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 1:
            if failure == 'transport':
                raise httpx.ReadTimeout('private credential')
            return httpx.Response(failure)
        return httpx.Response(201)
    monkeypatch.setattr(persistence.httpx, 'post', post)
    monkeypatch.setattr(persistence.time, 'sleep', delays.append)
    monkeypatch.setattr(persistence.random, 'uniform', lambda a, b: 0.25)
    write(models)
    assert len(calls) == 4 and calls[0] == calls[1]
    assert delays == [1.25]
    assert [c[0].rsplit('/', 1)[1] for c in calls] == ['assessments','assessments','reserve_snapshots','evidence_records']


def test_repeated_504_exhausts_three_attempts(models, http, monkeypatch):
    calls, delays = [], []
    def post(url, **kwargs):
        calls.append(kwargs['json'])
        return httpx.Response(504, text='private credential')
    monkeypatch.setattr(persistence.httpx, 'post', post)
    monkeypatch.setattr(persistence.time, 'sleep', delays.append)
    monkeypatch.setattr(persistence.random, 'uniform', lambda a,b: 0.5)
    with pytest.raises(persistence.PersistenceError) as caught:
        write(models)
    assert len(calls) == 3 and calls[0] == calls[1] == calls[2]
    assert delays == [1.5, 2.5]
    assert caught.value.http_status == 504 and caught.value.completed_tables == ()
    assert caught.value.outcome_unknown is True
    assert 'private credential' not in str(caught.value)


@pytest.mark.parametrize('status,code', [(401,None),(403,'42501'),(409,'23505'),(504,'23514')])
def test_auth_and_database_errors_not_retried(models, http, monkeypatch, status, code):
    calls=[]
    def post(url, **kwargs):
        calls.append(url)
        return httpx.Response(status, json={'code':code})
    monkeypatch.setattr(persistence.httpx, 'post', post)
    monkeypatch.setattr(persistence.time, 'sleep', lambda _: pytest.fail('Unexpected retry'))
    with pytest.raises(persistence.PersistenceError):
        write(models)
    assert len(calls)==1


@pytest.mark.parametrize('stage', [2,3])
def test_partial_write_504_is_never_retried(models, http, monkeypatch, stage):
    calls=[]
    def post(url, **kwargs):
        calls.append(url)
        return httpx.Response(504 if len(calls)==stage else 201)
    monkeypatch.setattr(persistence.httpx,'post',post)
    monkeypatch.setattr(persistence.time,'sleep',lambda _: pytest.fail('Partial write retried'))
    with pytest.raises(persistence.PersistenceError) as caught:
        write(models)
    assert len(calls)==stage
    assert caught.value.completed_tables==('assessments','reserve_snapshots')[:stage-1]
    assert caught.value.outcome_unknown is True


def test_ambiguous_commit_then_conflict_stops_without_second_table(models, http, monkeypatch):
    calls=[]
    def post(url, **kwargs):
        calls.append((url,kwargs['json']))
        return httpx.Response(504) if len(calls)==1 else httpx.Response(409,json={'code':'23505'})
    monkeypatch.setattr(persistence.httpx,'post',post)
    monkeypatch.setattr(persistence.time,'sleep',lambda _:None)
    with pytest.raises(persistence.PersistenceError) as caught:
        write(models)
    assert len(calls)==2 and calls[0]==calls[1]
    assert caught.value.http_status==409 and caught.value.error_code=='23505'
    assert caught.value.outcome_unknown is True
