"""Private REST persistence, independent of acquisition and scoring.

Three inserts are NOT an atomic transaction. Failures report confirmed stages;
transport failures can have an unknown commit outcome. No retries or overwrites.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from uuid import UUID, NAMESPACE_URL, uuid5

import httpx
from pydantic import ValidationError

from risk_oracle.config import load_settings
from risk_oracle.models import EvidenceRecord, PrecomputedAssessment, ReserveSnapshot, RpcSource


# Error response bodies may echo credentials or row data. Publish only fixed
# summaries, never raw message/details/hint text, headers, URLs or exception text.
_LOCAL_MESSAGES = {
    "SUPABASE_URL and SUPABASE_SERVICE_KEY must be configured.",
    "Invalid Supabase project URL configuration.",
    "Invalid Supabase service key configuration.",
    "Invalid shared model payload.",
    "A non-empty, trimmed methodology version is required.",
    "Assessment methodology version does not match the requested storage version.",
    "Assessment and snapshot identity do not match.",
    "Assessment contains unresolved evidence or observations.",
    "Duplicate evidence row identifiers.",
    "Persistence payload contains a configured credential.",
}
_CODE_MESSAGES = {
    "23502": "Required database column is null.",
    "23503": "Foreign-key constraint violation.",
    "23505": "Unique constraint violation; duplicate record rejected.",
    "23514": "Database check constraint violation; verify applied migrations and payload contract.",
    "42501": "Database permission denied; verify service-role credentials, grants and RLS.",
    "22P02": "Invalid input representation for a database column.",
    "22003": "Database numeric value out of range.",
    "42P01": "Database table does not exist.",
    "42703": "Database column does not exist.",
    "PGRST204": "Column missing from PostgREST schema cache.",
    "PGRST205": "Table missing from PostgREST schema cache.",
    "PGRST301": "JWT verification failed.",
    "PGRST302": "Authentication required; anonymous access is disabled.",
    "PGRST303": "JWT claims validation failed.",
}


class PersistenceError(RuntimeError):
    def __init__(self, message: str, *, table: str | None = None,
                 completed_tables: tuple[str, ...] = (), outcome_unknown: bool = False,
                 http_status: int | None = None, error_code: str | None = None):
        tables = ("assessments", "reserve_snapshots", "evidence_records")
        self.table = table if table in tables else None
        self.completed_tables = tuple(t for t in completed_tables if t in tables)
        self.outcome_unknown = bool(outcome_unknown)
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.error_code = error_code if isinstance(error_code, str) and re.fullmatch(r"[0-9A-Z]{5}|PGRST[0-9]{3}", error_code) else None
        if self.http_status is not None:
            summary = _CODE_MESSAGES.get(self.error_code) or {
                400: "Supabase rejected the request.",
                401: "Supabase authentication rejected; verify the project and service key.",
                403: "Supabase access denied; verify service-role credentials and database grants.",
                404: "Supabase REST resource not found.",
                409: "Database constraint conflict.",
                429: "Supabase rate limit exceeded.",
            }.get(self.http_status, "Supabase returned an unsuccessful HTTP response.")
            self.sanitized_message = summary
            super().__init__(f"Supabase write failed for {self.table}: HTTP {self.http_status}. {summary}")
        else:
            self.sanitized_message = ("Transport error; commit outcome unknown." if self.outcome_unknown
                                      else message if message in _LOCAL_MESSAGES
                                      else "Persistence failed; unrecognized error text suppressed.")
            super().__init__(self.sanitized_message)


def _response_error_code(response):
    """Retain only the machine error code; non-JSON/oversized bodies are omitted."""
    try:
        if len(response.content) > 65536:
            return None
        body = response.json()
        code = body.get("code") if isinstance(body, dict) else None
        return code if isinstance(code, str) and re.fullmatch(r"[0-9A-Z]{5}|PGRST[0-9]{3}", code) else None
    except (ValueError, UnicodeError):
        return None


@dataclass(frozen=True)
class WriteResult:
    assessment_id: UUID
    snapshot_id: UUID
    evidence_ids: tuple[UUID, ...]


def _row_id(identifier: str, scope: str) -> UUID:
    """Preserve UUID identifiers; deterministically map content IDs to row UUIDs."""
    try:
        return UUID(identifier)
    except ValueError:
        return uuid5(NAMESPACE_URL, f"risk-oracle:v0:{scope}:{identifier}")


def _assessment_evidence(assessment: PrecomputedAssessment, snapshot: ReserveSnapshot,
                         methodology_version: str) -> EvidenceRecord:
    # Preserve factors, hashes, limitations and complete freshness without schema changes.
    raw = assessment.model_dump_json()
    digest = "0x" + sha256(raw.encode()).hexdigest()
    payload = dict(
        id=f"precomputed-assessment:{assessment.id}", content_hash=digest,
        canonicalization_version="sha256-raw-result-utf8-v1",
        source=dict(kind="service_policy", reference=f"urn:risk-oracle:policy:{assessment.policy_hash}",
                    section="precomputed_assessment", revision=methodology_version,
                    document_hash=assessment.policy_hash),
        collected_at=assessment.computed_at, source_updated_at=None,
        run_id=snapshot.run_id, code_revision="unspecified",
        normalizer_version="supabase-writer-v1", parent_evidence_ids=assessment.evidence_ids,
        transformation="serialize_precomputed_assessment", outcome="success",
        raw_result=raw, error_code=None,
    )
    return EvidenceRecord(**payload)


class SupabaseWriter:
    def write(self, assessment: PrecomputedAssessment, snapshot: ReserveSnapshot,
              *, methodology_version: str) -> WriteResult:
        """Validate everything locally, then insert assessment, snapshot and evidence.

        ``created_at`` is assigned by Postgres. All model timestamps are preserved.
        A partial failure needs reconciliation before the assessment can be served.
        """
        settings = load_settings()
        if not settings.supabase_url or not settings.supabase_service_key:
            raise PersistenceError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be configured.")
        try:
            url = httpx.URL(settings.supabase_url)
            if url.scheme != "https" or not url.host or url.userinfo or url.query or url.fragment or url.path not in ("", "/"):
                raise ValueError
        except (httpx.InvalidURL, ValueError):
            raise PersistenceError("Invalid Supabase project URL configuration.") from None
        key = settings.supabase_service_key
        if any(char.isspace() for char in key) or not key.isascii():
            raise PersistenceError("Invalid Supabase service key configuration.")
        try:
            assessment = PrecomputedAssessment.model_validate(assessment.model_dump())
            snapshot = ReserveSnapshot.model_validate(snapshot.model_dump())
        except ValidationError:
            raise PersistenceError("Invalid shared model payload.") from None
        if not methodology_version.strip() or methodology_version != methodology_version.strip():
            raise PersistenceError("A non-empty, trimmed methodology version is required.")
        if assessment.methodology_version is not None and assessment.methodology_version != methodology_version:
            raise PersistenceError("Assessment methodology version does not match the requested storage version.")
        if assessment.snapshot_id != snapshot.id or assessment.subject != snapshot.subject:
            raise PersistenceError("Assessment and snapshot identity do not match.")
        evidence_ids = {record.id for record in snapshot.evidence}
        observation_ids = {item.id for item in snapshot.observations.values()}
        if not set(assessment.evidence_ids) <= evidence_ids or any(
            not set(factor.observation_ids) <= observation_ids for factor in assessment.factors
        ):
            raise PersistenceError("Assessment contains unresolved evidence or observations.")

        assessment_id = _row_id(assessment.id, "assessments")
        snapshot_id = _row_id(snapshot.id, f"{assessment_id}:reserve_snapshots")
        data = assessment.model_dump(mode="json")
        snapshot_data = snapshot.model_dump(mode="json")
        records = (*snapshot.evidence, _assessment_evidence(assessment, snapshot, methodology_version))
        row_ids = tuple(_row_id(record.id, f"{assessment_id}:evidence_records") for record in records)
        if len(set(row_ids)) != len(row_ids):
            raise PersistenceError("Duplicate evidence row identifiers.")
        batches = [
            ("assessments", [dict(
                id=str(assessment_id), chain="base", protocol=assessment.subject.protocol,
                asset=assessment.subject.asset, score=assessment.score, risk_level=assessment.risk_level,
                confidence=assessment.confidence, freshness_status=assessment.freshness_status,
                calculated_at=data["computed_at"], stale_after=data["freshness"]["expires_at"],
                block_number=str(snapshot.block.number), methodology_version=methodology_version,
            )]),
            ("reserve_snapshots", [dict(
                id=str(snapshot_id), assessment_id=str(assessment_id), asset=snapshot.subject.asset,
                block_number=str(snapshot.block.number), observed_at=snapshot_data["collected_at"],
                snapshot_json=snapshot_data,
            )]),
            ("evidence_records", [dict(
                id=str(row_id), assessment_id=str(assessment_id), evidence_type=record.source.kind,
                source=record.source.provider_id if isinstance(record.source, RpcSource) else record.source.kind,
                source_url=None if isinstance(record.source, RpcSource) else (
                    record.source.reference if record.source.reference.startswith("https://") else None),
                observed_at=record.model_dump(mode="json")["collected_at"],
                evidence_json=record.model_dump(mode="json"),
            ) for row_id, record in zip(row_ids, records, strict=True)]),
        ]
        # Do not let either configured credential leak into stored payloads.
        serialized = json.dumps(batches, ensure_ascii=False, allow_nan=False)
        if settings.supabase_url in serialized or key in serialized:
            raise PersistenceError("Persistence payload contains a configured credential.")
        headers = {"apikey": key, "Prefer": "return=minimal"}
        if not key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {key}"
        completed = []
        for table, rows in batches:
            try:
                response = httpx.post(str(url).rstrip("/") + "/rest/v1/" + table,
                                      headers=headers, json=rows, timeout=15.0, follow_redirects=False)
            except (httpx.RequestError, httpx.InvalidURL):
                raise PersistenceError(
                    f"Supabase write failed for {table}: transport error; commit outcome unknown.",
                    table=table, completed_tables=tuple(completed), outcome_unknown=True,
                ) from None
            if response.status_code not in (201, 204):
                raise PersistenceError(
                    f"Supabase write failed for {table}: HTTP {response.status_code}.",
                    table=table, completed_tables=tuple(completed),
                    http_status=response.status_code, error_code=_response_error_code(response),
                )
            completed.append(table)
        return WriteResult(assessment_id, snapshot_id, row_ids)
