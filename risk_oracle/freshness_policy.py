"""Snapshot acquisition-age policy only. No price-feed validity or scoring claims."""
from datetime import datetime, timedelta, timezone

from risk_oracle.models import Freshness, ReserveSnapshot


def snapshot_freshness(*, observed_at: datetime, calculated_at: datetime) -> Freshness:
    """Use explicit aware timestamps; no implicit clock or scoring invocation.

    Existing Freshness deadlines are exclusive. One microsecond (datetime's
    resolution) makes the policy's 25/45/90-minute upper bounds inclusive.
    """
    if observed_at.utcoffset() is None or calculated_at.utcoffset() is None:
        raise ValueError("Freshness timestamps must include timezones")
    observed_at = observed_at.astimezone(timezone.utc)
    calculated_at = calculated_at.astimezone(timezone.utc)
    age = calculated_at - observed_at
    if age < timedelta(0):
        raise ValueError("observed_at must not be later than calculated_at")
    if age <= timedelta(minutes=25):
        status = "FRESH"
    elif age <= timedelta(minutes=45):
        status = "DEGRADED"
    elif age <= timedelta(minutes=90):
        status = "STALE"
    else:
        status = "UNKNOWN"
    step = timedelta(microseconds=1)
    known = status != "UNKNOWN"
    return Freshness(
        status=status, evaluated_at=calculated_at,
        fresh_until=observed_at + timedelta(minutes=25) + step if known else None,
        expires_at=observed_at + timedelta(minutes=45) + step if known else None,
        unknown_after=observed_at + timedelta(minutes=90) + step if known else None,
        reasons=("snapshot-age-v0; acquisition age only, not evidence completeness or feed validity",),
    )


def apply_snapshot_freshness(snapshot: ReserveSnapshot, *, calculated_at: datetime) -> ReserveSnapshot:
    """Set snapshot age freshness without changing observations or evidence.

    collected_at maps to the storage column observed_at. Missing evidence and
    unsupported risk factors remain unchanged; fresh does not imply scoreable.
    """
    data = snapshot.model_dump()
    data["freshness"] = snapshot_freshness(
        observed_at=snapshot.collected_at, calculated_at=calculated_at,
    ).model_dump()
    return ReserveSnapshot.model_validate(data)
