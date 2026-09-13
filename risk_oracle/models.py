"""Offline shared contracts. No acquisition, storage, or scoring is performed here.

Integer quantities use Python integers and decimal strings on the JSON wire.
Freshness evaluation requires an explicit clock supplied by the caller.
"""
from datetime import datetime, timezone
from enum import StrEnum
import re
from typing import Annotated, Literal

from pydantic import (
    AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field,
    PlainSerializer, StrictBool, StrictInt, StrictStr, WithJsonSchema,
    field_serializer, field_validator, model_validator,
)


def _uint256(value: object) -> int:
    if isinstance(value, str) and re.fullmatch(r"0|[1-9][0-9]*", value):
        value = int(value)
    if type(value) is not int or not 0 <= value < 2**256:
        raise ValueError("Expected a uint256 integer or canonical decimal string")
    return value


def _utc(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("Timestamp must include a timezone")
    return value.astimezone(timezone.utc)


UInt256 = Annotated[
    int, BeforeValidator(_uint256),
    PlainSerializer(str, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": r"^(0|[1-9][0-9]*)$",
                    "description": "Decimal uint256, maximum 2**256-1"}),
]
Timestamp = Annotated[datetime, AfterValidator(_utc)]
Text = Annotated[str, Field(strict=True, min_length=1, pattern=r"\S")]
Hash = Annotated[str, Field(pattern=r"^0x[0-9a-f]{64}$")]
Address = Annotated[str, Field(pattern=r"^0x[0-9a-f]{40}$")]
EvidenceIds = Annotated[tuple[Text, ...], Field(min_length=1)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FreshnessStatus(StrEnum):
    FRESH = "FRESH"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class Freshness(Contract):
    status: FreshnessStatus
    evaluated_at: Timestamp
    fresh_until: Timestamp | None
    expires_at: Timestamp | None
    reasons: tuple[Text, ...]
    unknown_after: Timestamp | None = None

    @model_validator(mode="after")
    def valid_deadlines(self):
        if self.status == FreshnessStatus.UNKNOWN:
            if self.fresh_until is not None or self.expires_at is not None:
                raise ValueError("UNKNOWN must not claim freshness deadlines")
        else:
            if self.fresh_until is None or self.expires_at is None:
                raise ValueError("Known freshness requires both deadlines")
            if self.fresh_until > self.expires_at:
                raise ValueError("fresh_until must not exceed expires_at")
            if self.evaluated_at >= self.expires_at and self.status != FreshnessStatus.STALE:
                raise ValueError("Expired data must be STALE")
            if self.status == FreshnessStatus.STALE and self.evaluated_at < self.expires_at:
                raise ValueError("STALE requires an expired deadline")
            if self.status == FreshnessStatus.FRESH and self.evaluated_at >= self.fresh_until:
                raise ValueError("FRESH requires an unexpired fresh_until")
        if self.unknown_after is not None:
            if self.expires_at is None or self.unknown_after < self.expires_at:
                raise ValueError("unknown_after requires ordered known deadlines")
            if self.evaluated_at >= self.unknown_after:
                raise ValueError("Expired unknown_after must use UNKNOWN with null deadlines")
        if self.status != FreshnessStatus.FRESH and not self.reasons:
            raise ValueError("Non-FRESH status requires a reason")
        return self

    def status_at(self, at: datetime) -> FreshnessStatus:
        at = _utc(at)
        if at < self.evaluated_at:
            raise ValueError("Cannot evaluate freshness before evaluated_at")
        if self.status == FreshnessStatus.UNKNOWN:
            return FreshnessStatus.UNKNOWN
        if self.unknown_after is not None and at >= self.unknown_after:
            return FreshnessStatus.UNKNOWN
        if at >= self.expires_at:
            return FreshnessStatus.STALE
        if at >= self.fresh_until or self.status == FreshnessStatus.DEGRADED:
            return FreshnessStatus.DEGRADED
        return FreshnessStatus.FRESH


class BlockRef(Contract):
    chain_id: Literal[8453]
    number: UInt256
    hash: Hash
    parent_hash: Hash
    timestamp: Timestamp
    finality: Literal["unsafe", "safe", "finalized", "unknown"]
    canonicality_checked_at: Timestamp

    @model_validator(mode="after")
    def time_order(self):
        if self.canonicality_checked_at < self.timestamp:
            raise ValueError("Canonicality check precedes block timestamp")
        return self

    @property
    def identity(self) -> tuple[int, int, str]:
        return self.chain_id, self.number, self.hash


class Unit(Contract):
    name: Text
    decimals: Annotated[int, Field(strict=True, ge=0, le=255)]
    quote_currency: Text | None


class RpcSource(Contract):
    kind: Literal["onchain_rpc"]
    # A non-secret identifier, never an endpoint URL or request headers.
    provider_id: Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]+$")]
    block: BlockRef
    contract: Address
    method: Text
    call_signature: Text
    call_data: Annotated[str, Field(pattern=r"^0x(?:[0-9a-f]{2})*$")]
    abi_hash: Hash
    decoder_version: Text


class DocumentSource(Contract):
    kind: Literal["official_document", "service_policy"]
    reference: Text
    section: Text
    revision: Text
    document_hash: Hash


class EvidenceRecord(Contract):
    id: Text
    content_hash: Hash
    canonicalization_version: Text
    source: Annotated[RpcSource | DocumentSource, Field(discriminator="kind")]
    collected_at: Timestamp
    source_updated_at: Timestamp | None
    run_id: Text
    code_revision: Text
    normalizer_version: Text
    parent_evidence_ids: tuple[Text, ...]
    transformation: Text | None
    outcome: Literal["success", "failure"]
    # Exact response bytes/text; callers must sanitize secrets before constructing.
    raw_result: str | None
    error_code: Text | None

    @model_validator(mode="after")
    def valid_outcome(self):
        if self.outcome == "success":
            if self.raw_result is None or self.error_code is not None:
                raise ValueError("Successful evidence requires raw_result and no error")
        elif self.raw_result is not None or self.error_code is None:
            raise ValueError("Failed evidence requires a safe error code and no raw result")
        return self


class Observation(Contract):
    id: Text
    field: Text
    state: Literal["present", "missing", "invalid", "stale", "unsupported", "not_applicable"]
    datatype: Literal["uint256", "int256", "boolean", "text"]
    value: StrictInt | StrictBool | StrictStr | None
    unit: Unit
    block: BlockRef | None
    collected_at: Timestamp
    source_updated_at: Timestamp | None
    freshness: Freshness
    evidence_ids: EvidenceIds
    reason: Text | None

    @field_validator("value", mode="before")
    @classmethod
    def decode_integer(cls, value, info):
        datatype = info.data.get("datatype")
        if value is None:
            return None
        if datatype == "uint256":
            return _uint256(value)
        if datatype == "int256":
            if isinstance(value, str) and re.fullmatch(r"0|-?[1-9][0-9]*", value):
                value = int(value)
            if type(value) is not int or not -(2**255) <= value < 2**255:
                raise ValueError("Expected an int256 integer or canonical decimal string")
        if datatype == "boolean" and type(value) is not bool:
            raise ValueError("Expected a boolean")
        if datatype == "text" and type(value) is not str:
            raise ValueError("Expected text")
        return value

    @field_serializer("value", when_used="json")
    def encode_value(self, value) -> str | bool | None:
        return str(value) if type(value) is int else value

    @model_validator(mode="after")
    def valid_state(self):
        available = self.state in ("present", "stale")
        if available != (self.value is not None):
            raise ValueError("Only present/stale observations carry a value")
        if self.state != "present" and self.reason is None:
            raise ValueError("Unavailable or stale observations require a reason")
        if not available and self.freshness.status != FreshnessStatus.UNKNOWN:
            raise ValueError("Unavailable observations must have UNKNOWN freshness")
        if self.state == "stale" and self.freshness.status != FreshnessStatus.STALE:
            raise ValueError("Stale observations require STALE freshness")
        if self.state == "present" and self.freshness.status == FreshnessStatus.STALE:
            raise ValueError("Expired observations must use stale state")
        return self


class ReserveSubject(Contract):
    chain_id: Literal[8453]
    protocol: Literal["aave-v3"]
    pool: Address
    asset: Address


class ReserveSnapshot(Contract):
    id: Text
    subject: ReserveSubject
    block: BlockRef
    collected_at: Timestamp
    run_id: Text
    manifest_hash: Hash
    acquisition_status: Literal["complete", "partial", "failed"]
    observations: Annotated[dict[Text, Observation], Field(min_length=1)]
    evidence: Annotated[tuple[EvidenceRecord, ...], Field(min_length=1)]
    freshness: Freshness

    @model_validator(mode="after")
    def coherent_state(self):
        evidence = {record.id: record for record in self.evidence}
        if len(evidence) != len(self.evidence):
            raise ValueError("Duplicate evidence IDs")
        observation_ids = [item.id for item in self.observations.values()]
        if len(set(observation_ids)) != len(observation_ids):
            raise ValueError("Duplicate observation IDs")
        for record in self.evidence:
            if record.run_id != self.run_id:
                raise ValueError("Evidence from a different acquisition run")
            if isinstance(record.source, RpcSource) and record.source.block != self.block:
                raise ValueError("Mixed-block evidence")
            if any(parent not in evidence for parent in record.parent_evidence_ids):
                raise ValueError("Unresolved parent evidence")
        for name, observation in self.observations.items():
            if name != observation.field:
                raise ValueError("Observation key must match field")
            if observation.block is not None and observation.block != self.block:
                raise ValueError("Mixed-block observation")
            for reference in observation.evidence_ids:
                if reference not in evidence:
                    raise ValueError("Unresolved observation evidence")
                source = evidence[reference].source
                if isinstance(source, RpcSource) and observation.block != source.block:
                    raise ValueError("On-chain observation requires matching block provenance")
            if observation.block is not None and not any(
                isinstance(evidence[ref].source, RpcSource) for ref in observation.evidence_ids
            ):
                raise ValueError("On-chain observation requires RPC evidence")
        if self.acquisition_status == "complete" and any(
            item.state not in ("present", "not_applicable") for item in self.observations.values()
        ):
            raise ValueError("Complete acquisition cannot contain unavailable required data")
        return self


class FactorResult(Contract):
    factor: Text
    status: Literal["evaluated", "unknown"]
    score: Annotated[float, Field(strict=True, ge=0, le=100, allow_inf_nan=False)] | None
    explanation: Text
    rule_id: Text
    observation_ids: tuple[Text, ...]
    evidence_ids: tuple[Text, ...]

    @model_validator(mode="after")
    def score_presence(self):
        if self.status == "evaluated" and (not self.observation_ids or not self.evidence_ids):
            raise ValueError("Evaluated factors require observation and evidence provenance")
        if (self.status == "evaluated") != (self.score is not None):
            raise ValueError("Only evaluated factors have a score")
        return self


class PrecomputedAssessment(Contract):
    # None preserves legacy payloads without falsely labelling them v0.1.
    methodology_version: Text | None = None
    id: Text
    subject: ReserveSubject
    snapshot_id: Text
    snapshot_hash: Hash
    policy_hash: Hash
    input_hash: Hash
    computed_at: Timestamp
    freshness: Freshness
    score: Annotated[float, Field(strict=True, ge=0, le=100, allow_inf_nan=False)] | None
    risk_level: Literal["low", "moderate", "high", "critical", "unknown"]
    confidence: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)] | None
    freshness_status: Literal["fresh", "degraded", "stale", "unknown"]
    factors: Annotated[tuple[FactorResult, ...], Field(min_length=1)]
    evidence_ids: EvidenceIds
    limitations: tuple[Text, ...]

    @model_validator(mode="after")
    def consistent_result(self):
        if self.freshness.evaluated_at != self.computed_at:
            raise ValueError("Assessment freshness must be evaluated at computed_at")
        if self.freshness_status != self.freshness.status.value.lower():
            raise ValueError("freshness_status must agree with freshness metadata")
        unknown = self.risk_level == "unknown" or self.score is None
        if unknown:
            if self.score is not None or self.risk_level != "unknown" or self.confidence is not None:
                raise ValueError("UNKNOWN assessments require null score/confidence and unknown risk_level")
        elif self.freshness_status == "unknown":
            raise ValueError("Unknown data freshness cannot support a known assessment")
        elif self.score is None or self.risk_level == "unknown":
            raise ValueError("Known assessments require a score and risk_level")
        if self.score is not None:
            from risk_oracle.risk_engine import risk_level_for_score
            if self.risk_level != risk_level_for_score(self.score):
                raise ValueError("Score and risk_level must agree with the existing scorer")
        if any(factor.status == "unknown" for factor in self.factors) and not unknown:
            raise ValueError("Missing required factor evidence requires an UNKNOWN assessment")
        if len({factor.factor for factor in self.factors}) != len(self.factors):
            raise ValueError("Duplicate factor IDs")
        if any(ref not in self.evidence_ids for factor in self.factors for ref in factor.evidence_ids):
            raise ValueError("Factor evidence must be included in assessment evidence")
        return self


def shared_json_schema() -> dict:
    """Return the serialization contract for all five models (no file I/O)."""
    from pydantic.json_schema import models_json_schema

    _, schema = models_json_schema(
        [(model, "serialization") for model in (
            Observation, EvidenceRecord, BlockRef, ReserveSnapshot, PrecomputedAssessment
        )], title="Risk Oracle shared contracts",
    )
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema}
