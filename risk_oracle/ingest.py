"""One production v0.1 ingestion cycle; importing this module performs no I/O.

Run with python -m risk_oracle.ingest. No automatic retries: persistence can
partially commit, so failed/ambiguous writes require read-only reconciliation.
"""
from datetime import datetime, timezone
import json
import os
import re
import sys
from uuid import uuid4

from risk_oracle.config import load_settings
from risk_oracle.persistence import PersistenceError, SupabaseWriter
from risk_oracle.providers.aave_v3_base import AaveV3BaseCollector
from risk_oracle.scoring_v01 import METHODOLOGY_VERSION, assess_snapshot


class IngestionError(RuntimeError):
    """Safe diagnostic fields only; never retain provider error text."""
    def __init__(self, stage, *, completed_tables=(), outcome_unknown=False, persistence_error=None):
        super().__init__(f"Ingestion failed during {stage}.")
        self.stage = stage
        self.completed_tables = tuple(table for table in completed_tables
                                      if table in ("assessments", "reserve_snapshots", "evidence_records"))
        self.outcome_unknown = bool(outcome_unknown)
        self.table = persistence_error.table if persistence_error is not None else None
        self.http_status = persistence_error.http_status if persistence_error is not None else None
        self.error_code = persistence_error.error_code if persistence_error is not None else None
        self.sanitized_message = (persistence_error.sanitized_message if persistence_error is not None
                                  else f"Ingestion failed during {stage}.")


def run_cycle(*, code_revision: str, run_id: str | None = None, clock=None) -> dict:
    """Collect once, assess once, write once. A valid UNKNOWN is persisted unchanged."""
    stage = "configuration"
    try:
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", code_revision):
            raise ValueError("A commit SHA is required")
        settings = load_settings()
        if any(not value or not value.strip() for value in (
            settings.base_rpc_url, settings.supabase_url, settings.supabase_service_key
        )):
            raise ValueError("Required configuration is missing")
        now = clock if clock is not None else lambda: datetime.now(timezone.utc)
        stage = "collection"
        # collect() already builds a coherent ReserveSnapshot and applies freshness.
        snapshot = AaveV3BaseCollector().collect(
            run_id=run_id or str(uuid4()), code_revision=code_revision)
        stage = "scoring"
        assessment = assess_snapshot(snapshot, calculated_at=now())
        if assessment.methodology_version != "v0.1" or METHODOLOGY_VERSION != "v0.1":
            raise ValueError("Unexpected methodology version")
        stage = "persistence"
        written = SupabaseWriter().write(assessment, snapshot, methodology_version="v0.1")
        return dict(status="ok", assessment_id=str(written.assessment_id),
                    snapshot_id=str(written.snapshot_id), evidence_records=len(written.evidence_ids),
                    block_number=str(snapshot.block.number), score=assessment.score,
                    risk_level=assessment.risk_level, confidence=assessment.confidence,
                    freshness=assessment.freshness_status, methodology_version="v0.1")
    except PersistenceError as error:
        raise IngestionError(stage, completed_tables=error.completed_tables,
                             outcome_unknown=error.outcome_unknown, persistence_error=error) from None
    except Exception:
        # Validation and transport exceptions can contain credential-bearing input.
        raise IngestionError(stage) from None


def main() -> int:
    try:
        result = run_cycle(code_revision=os.environ.get("GITHUB_SHA", ""))
    except IngestionError as error:
        print(json.dumps(dict(status="failed", stage=error.stage,
                              completed_tables=error.completed_tables,
                              commit_outcome_unknown=error.outcome_unknown,
                              table=error.table, http_status=error.http_status,
                              error_code=error.error_code, message=error.sanitized_message,
                              error="Ingestion did not complete; no automatic retry. Reconcile any partial writes before rerunning.")),
              file=sys.stderr)
        return 1
    print(json.dumps(result, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
