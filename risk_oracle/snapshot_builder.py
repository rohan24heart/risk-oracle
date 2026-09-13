"""Build coherent reserve snapshots from already collected, offline observations."""
from collections.abc import Mapping, Sequence, Set
from datetime import datetime

from risk_oracle.models import (
    BlockRef, EvidenceRecord, Freshness, FreshnessStatus, Observation,
    ReserveSnapshot, ReserveSubject,
)


def build_reserve_snapshot(
    *,
    snapshot_id: str,
    subject: ReserveSubject,
    block: BlockRef,
    collected_at: datetime,
    run_id: str,
    manifest_hash: str,
    observations: Mapping[str, Observation],
    evidence: Sequence[EvidenceRecord],
    critical_fields: Set[str],
    freshness: Freshness,
) -> ReserveSnapshot:
    """Assemble supplied data without acquisition, interpolation, or scoring.

    The caller explicitly identifies critical fields and supplies the snapshot's
    freshness ceiling. Input deadlines may shorten that ceiling, never extend it.
    Omitted critical fields produce UNKNOWN with named reasons; no observation,
    unit, or provenance is fabricated. Entirely empty inputs remain invalid under
    the existing ReserveSnapshot contract.
    """
    if not critical_fields or any(not isinstance(name, str) or not name.strip()
                                  for name in critical_fields):
        raise ValueError("critical_fields must contain non-empty field names")

    # Revalidate through plain data so nested model_copy/construct instances do
    # not bypass the shared model's provenance and block-consistency checks.
    inputs = {name: Observation.model_validate(item.model_dump())
              for name, item in observations.items()}
    missing = sorted(name for name in critical_fields if name not in inputs
                     or inputs[name].value is None)
    partial = bool(missing) or any(item.state not in ("present", "not_applicable")
                                   for item in inputs.values())
    windows = [freshness, *(item.freshness for item in inputs.values())]
    statuses = [window.status_at(freshness.evaluated_at) for window in windows]
    reasons = list(dict.fromkeys(reason for window in windows for reason in window.reasons))
    reasons.extend(f"critical_input_missing:{name}" for name in missing)

    if missing or FreshnessStatus.UNKNOWN in statuses:
        status = FreshnessStatus.UNKNOWN
        fresh_until = expires_at = None
    else:
        fresh_until = min(window.fresh_until for window in windows)
        expires_at = min(window.expires_at for window in windows)
        if FreshnessStatus.STALE in statuses:
            status = FreshnessStatus.STALE
        elif partial or FreshnessStatus.DEGRADED in statuses:
            status = FreshnessStatus.DEGRADED
        else:
            status = FreshnessStatus.FRESH
    if status != FreshnessStatus.FRESH and not reasons:
        reasons.append(f"snapshot_{status.value.lower()}")
    combined_freshness = Freshness(
        status=status, evaluated_at=freshness.evaluated_at,
        fresh_until=fresh_until, expires_at=expires_at, reasons=tuple(reasons),
    )
    acquisition_status = "partial" if partial else "complete"
    if not any(item.value is not None for item in inputs.values()):
        acquisition_status = "failed"

    return ReserveSnapshot.model_validate(dict(
        id=snapshot_id, subject=subject.model_dump(), block=block.model_dump(),
        collected_at=collected_at, run_id=run_id, manifest_hash=manifest_hash,
        observations={name: item.model_dump() for name, item in inputs.items()},
        evidence=[record.model_dump() for record in evidence],
        acquisition_status=acquisition_status, freshness=combined_freshness.model_dump(),
    ))
