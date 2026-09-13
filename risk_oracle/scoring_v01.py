"""v0.1 reserve-condition index. Pure offline scoring, never the legacy mock scorer.

Oracle risk measures feed freshness/integrity and verified adapter behavior,
NOT independent economic accuracy of USDC's market price. See docs/methodology-v0.1.md.
"""
from datetime import datetime
from fractions import Fraction as F
from hashlib import sha256
import json
from uuid import NAMESPACE_URL, uuid5

from risk_oracle.freshness_policy import snapshot_freshness
from risk_oracle.models import FactorResult, PrecomputedAssessment, ReserveSnapshot
from risk_oracle.providers.aave_v3_base import POOL, USDC

METHODOLOGY_VERSION = "v0.1"
FACTOR_NAMES = ("oracle_risk", "liquidity_utilization_risk", "collateral_configuration_risk",
                "operational_restriction_risk")
POLICY = {
    "version": METHODOLOGY_VERSION,
    "scope": "Base native USDC Aave V3; reserve-condition index, not loss probability",
    "oracle": "verified stable cap; invalid time UNKNOWN; nonpositive answers or transformation mismatch 100; otherwise 100*clip(age/heartbeat-1)",
    "liquidity": "u=max(D/(D+V),clip(1-C/S)); 50*u/K below kink, 50+50*(u-K)/(1-K) above; S>0,D+V>0,0<K<1",
    "collateral": "all zero 0; invalid 0<=l<=t<=1,b>1 or t*b>=1:100; l=t>0:50; otherwise 0",
    "restriction": "max(paused/inactive:100,frozen:50,ordinary borrowing disabled or nonzero cap reached:25,otherwise:0)",
    "weights": ["1/4"] * 4, "critical_floor": 75,
    "rounding": "exact rational until final score; 2 decimals half-up; classify rounded score",
    "thresholds": [25, 50, 75],
    "confidence": "4/7 times fresh:1,degraded:3/4; stale/unknown not actionable -> null score/confidence",
    "freshness": "age from earlier of acquisition/block; <=25m fresh,<=45m degraded,<=90m stale,else unknown",
    "missing": "any required factor UNKNOWN -> null score/confidence; no reweighting",
}
LIMITATIONS = (
    "Experimental reserve-condition index, not loss probability or investment safety.",
    "Oracle risk measures feed freshness/integrity and verified adapter behavior, NOT independent economic accuracy of USDC's market price.",
    "UNKNOWN: live sequencer status and recovery; RPC reachability is not sequencer health.",
    "UNKNOWN: fallback-oracle behavior; primary anomalies do not prove fallback failure.",
    "UNKNOWN: borrower/collateral exposure, liquidation execution and reserve deficits.",
    "Default collateral/configuration checks do not measure collateral backing USDC loans; eMode coverage is incomplete.",
    "Confidence is policy-defined evidence coverage, not a calibrated probability; weights and severity mappings are uncalibrated.",
    "Off-chain metadata is verified as of retrieval, not guaranteed current; upgrades and sampling gaps can invalidate interpretation.",
)


def _hash(value):
    return "0x" + sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _clip(value):
    return min(F(1), max(F(0), value))


def _rounded(value):
    # Nonnegative exact rational, half-up to two decimal places.
    cents = (value.numerator * 200 + value.denominator) // (2 * value.denominator)
    return float(F(cents, 100))


def _level(score):
    return "low" if score < 25 else "moderate" if score < 50 else "high" if score < 75 else "critical"


class _Unknown(ValueError):
    pass


class _Inputs:
    def __init__(self, snapshot, at):
        self.snapshot, self.at = snapshot, at
        self.used = {}
        self.records = {item.id: item for item in snapshot.evidence}

    def get(self, name, datatype, unit, decimals=None, *, offchain=False):
        item = self.snapshot.observations.get(name)
        if item is None:
            raise _Unknown(f"Missing observation: {name}")
        self.used[name] = item
        if item.state != "present":
            raise _Unknown(f"Unavailable observation: {name} ({item.state})")
        if item.datatype != datatype or item.unit.name != unit or (decimals is not None and item.unit.decimals != decimals):
            raise _Unknown(f"Invalid datatype or units: {name}")
        if unit == "USD" and item.unit.quote_currency != "USD":
            raise _Unknown(f"Invalid quote currency: {name}")
        if item.collected_at > self.at or (item.source_updated_at is not None and item.source_updated_at > self.at):
            raise _Unknown(f"Future observation timestamp: {name}")
        if not offchain and item.block != self.snapshot.block:
            raise _Unknown(f"Missing block provenance: {name}")
        refs, pending = set(), list(item.evidence_ids)
        while pending:
            ref = pending.pop()
            if ref in refs:
                continue
            refs.add(ref)
            record = self.records[ref]
            if record.outcome != "success" or record.collected_at > self.at:
                raise _Unknown(f"Failed or future evidence: {name}")
            pending.extend(record.parent_evidence_ids)
        return item.value

    def integer(self, name, unit="integer", decimals=0):
        return self.get(name, "uint256", unit, decimals)

    def flag(self, name):
        return self.get(name, "boolean", "boolean", 0)

    def address(self, name):
        import re
        value = self.get(name, "text", "address", 0)
        if not re.fullmatch(r"0x[0-9a-f]{40}", value) or int(value, 16) == 0:
            raise _Unknown(f"Invalid or zero address: {name}")
        return value

    def evidence_ids(self):
        refs, pending = set(), [ref for item in self.used.values() for ref in item.evidence_ids]
        while pending:
            ref = pending.pop()
            if ref not in refs:
                refs.add(ref)
                pending.extend(self.records[ref].parent_evidence_ids)
        return tuple(sorted(refs))


def _oracle(x):
    x.address("aave_price_oracle")
    x.address("oracle_source")
    upstream = x.address("oracle_upstream_feed")
    proxy = x.address("oracle_feed_proxy")
    x.address("oracle_feed_aggregator")
    kind = x.get("oracle_source_interface", "text", "classification", 0)
    proxy_kind = x.get("oracle_feed_proxy_type", "text", "classification", 0)
    chainlink = x.flag("oracle_feed_is_chainlink")
    if kind != "aave_price_cap_adapter_stable" or proxy_kind != "Chainlink EACAggregatorProxy" or not chainlink or proxy != upstream:
        raise _Unknown("Unsupported or unverified adapter/feed relationship")
    feed_scale = x.integer("oracle_feed_decimals", "decimal_places")
    source_scale = x.integer("oracle_source_decimals", "decimal_places")
    if feed_scale > 255 or source_scale > 255:
        raise _Unknown("Invalid oracle decimals")
    feed = x.get("oracle_feed_latest_answer", "int256", "USD", feed_scale)
    source = x.get("oracle_source_latest_answer", "int256", "USD", source_scale)
    cap = x.get("oracle_source_price_cap", "int256", "USD", source_scale)
    updated = x.integer("oracle_feed_updated_at", "unix_seconds")
    heartbeat = x.get("oracle_feed_heartbeat", "uint256", "seconds", 0, offchain=True)
    block_time = int(x.snapshot.block.timestamp.timestamp())
    if not 0 < updated <= block_time or heartbeat <= 0 or cap < 0:
        raise _Unknown("Invalid feed timestamp, heartbeat or adapter cap")
    if feed <= 0 or source <= 0:
        return F(100), "Nonpositive primary/upstream answer; fallback behavior remains UNKNOWN."
    if F(source, 10**source_scale) != min(F(feed, 10**feed_scale), F(cap, 10**source_scale)):
        return F(100), "Source contradicts verified stable-cap transformation."
    age = block_time - updated
    return 100 * _clip(F(age - heartbeat, heartbeat)), f"Feed age={age}s; heartbeat={heartbeat}s; verified adapter transformation matched."


def _liquidity(x):
    supplied, debt, cash, virtual = [x.integer(name, "USDC", 6) for name in
        ("total_supplied", "variable_debt", "available_liquidity", "virtual_available_liquidity")]
    kink = F(x.integer("optimal_utilization", "ray", 27), 10**27)
    if supplied <= 0 or debt + virtual <= 0 or not 0 < kink < 1:
        raise _Unknown("Undefined liquidity denominator or invalid utilization kink")
    u = F(debt, debt + virtual)
    cash_u = _clip(1 - F(cash, supplied))
    effective = max(u, cash_u)
    score = 50 * effective / kink if effective <= kink else 50 + 50 * (effective - kink) / (1 - kink)
    return score, f"U={u}; cash pressure={cash_u}; effective utilization={effective}; kink={kink}. Actual cash is not inferred from supply minus debt."


def _collateral(x):
    l, t, b = [F(x.integer(name, "basis_points", 4), 10000) for name in
               ("ltv", "liquidation_threshold", "liquidation_bonus")]
    if l == t == b == 0:
        return F(0), "Default collateral disabled; not proof of borrower safety."
    details = f"Default buffer={t-l}; simplified liquidation headroom={1-t*b}. "
    if not (0 <= l <= t <= 1 and b > 1) or t * b >= 1:
        return F(100), details + "Configuration relationship failure or nonpositive simplified headroom."
    if l == t and t > 0:
        return F(50), details + "No default borrowing-to-liquidation buffer."
    return F(0), details + "Default configuration consistency screen passed; borrower exposure UNKNOWN."


def _restriction(x):
    # Read all required flags/caps before applying severity, including when paused.
    active, paused, frozen, borrowing = [x.flag(name) for name in
        ("reserve_active", "reserve_paused", "reserve_frozen", "borrowing_enabled")]
    supply_cap = x.integer("supply_cap", "USDC_whole_tokens")
    borrow_cap = x.integer("borrow_cap", "USDC_whole_tokens")
    supply_used = x.integer("supply_cap_total_supplied", "USDC", 6) if supply_cap else None
    borrow_used = x.integer("variable_debt", "USDC", 6) if borrow_cap else None
    cap_reached = (supply_cap > 0 and supply_used >= supply_cap * 10**6) or (borrow_cap > 0 and borrow_used >= borrow_cap * 10**6)
    score = 100 if paused or not active else 50 if frozen else 25 if not borrowing or cap_reached else 0
    return F(score), f"active={active}, paused={paused}, frozen={frozen}, ordinary borrowing={borrowing}, nonzero cap reached={bool(cap_reached)}; zero caps are uncapped. Restrictions do not prove insolvency."


def calculate_confidence(*, factors_complete: bool, freshness_status: str):
    """Separate evidence coverage calculation; never scales the risk score."""
    if not factors_complete:
        return None
    if freshness_status not in ("fresh", "degraded", "stale", "unknown"):
        raise ValueError("Invalid freshness status")
    return float(F(4, 7) * {"fresh": F(1), "degraded": F(3, 4), "stale": F(0), "unknown": F(0)}[freshness_status])


def assess_snapshot(snapshot: ReserveSnapshot, *, calculated_at: datetime) -> PrecomputedAssessment:
    """Assess one existing snapshot. No RPC, persistence, implicit clock or mock scorer."""
    # Revalidate even frozen models: nested mappings/model_copy can bypass validation.
    snapshot = ReserveSnapshot.model_validate(snapshot.model_dump())
    if snapshot.subject.asset != USDC or snapshot.subject.pool != POOL:
        raise ValueError("v0.1 supports only native USDC on Aave V3 Base")
    if calculated_at.utcoffset() is None:
        raise ValueError("calculated_at must include a timezone")
    if snapshot.collected_at > calculated_at or snapshot.block.canonicality_checked_at > calculated_at:
        raise ValueError("Future snapshot/canonicality timestamps")
    freshness = snapshot_freshness(observed_at=min(snapshot.collected_at, snapshot.block.timestamp), calculated_at=calculated_at)
    results, exact_scores = [], []
    for name, evaluate in zip(FACTOR_NAMES, (_oracle, _liquidity, _collateral, _restriction), strict=True):
        x = _Inputs(snapshot, calculated_at)
        score = None
        try:
            if snapshot.block.finality not in ("safe", "finalized"):
                raise _Unknown("Unverified block finality")
            score, explanation = evaluate(x)
        except _Unknown as error:
            explanation = str(error)
        results.append(FactorResult(factor=name, status="unknown" if score is None else "evaluated",
            score=None if score is None else float(score), explanation=explanation,
            rule_id=f"{METHODOLOGY_VERSION}:{name}", observation_ids=tuple(item.id for item in x.used.values()),
            evidence_ids=x.evidence_ids()))
        exact_scores.append(score)
    complete = all(score is not None for score in exact_scores)
    score = None
    status = freshness.status.value.lower()
    confidence = calculate_confidence(factors_complete=complete, freshness_status=status)
    if complete and status in ("fresh", "degraded"):
        weighted = sum(exact_scores, F(0)) / 4
        score = _rounded(max(weighted, F(75)) if F(100) in exact_scores else weighted)
    else:
        # Shared canonical UNKNOWN contract requires null confidence as well.
        confidence = None
    wire = snapshot.model_dump(mode="json")
    snapshot_hash, policy_hash = _hash(wire), _hash(POLICY)
    identity = dict(snapshot_hash=snapshot_hash, policy_hash=policy_hash, calculated_at=freshness.evaluated_at.isoformat())
    return PrecomputedAssessment(
        id=str(uuid5(NAMESPACE_URL, _hash(identity))), methodology_version=METHODOLOGY_VERSION,
        subject=snapshot.subject, snapshot_id=snapshot.id, snapshot_hash=snapshot_hash,
        input_hash=snapshot_hash, policy_hash=policy_hash, computed_at=calculated_at, freshness=freshness,
        freshness_status=status, score=score, risk_level="unknown" if score is None else _level(score),
        confidence=confidence, factors=tuple(results), evidence_ids=tuple(item.id for item in snapshot.evidence),
        limitations=LIMITATIONS + (() if status in ("fresh", "degraded") else ("Snapshot is not current/actionable; no current score issued.",)),
    )
