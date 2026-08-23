"""Immutable, dependency-neutral event contracts used by Glassbox."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

_ULID_PATTERN = re.compile(r"^[0-7][0-9A-HJKMNPQRSTVWXYZ]{25}$")
_UTC_OFFSET = timedelta(0)


class FrozenDict(Mapping[str, Any]):
    """A read-only mapping used for canonical event payload data."""

    def __init__(self, values: Mapping[str, Any] | None = None) -> None:
        self._values = MappingProxyType(dict(values or {}))

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


def _deep_freeze(value: Any) -> Any:
    """Validate and freeze a JSON-compatible payload value."""
    if isinstance(value, Mapping):
        frozen_values: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("payload mapping keys must be strings")
            frozen_values[key] = _deep_freeze(item)
        return FrozenDict(frozen_values)
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError("payload values must be JSON-compatible")


def _json_payload(value: Any) -> Any:
    """Return immutable payload data as ordinary JSON-compatible containers."""
    if isinstance(value, Mapping):
        return {key: _json_payload(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_payload(item) for item in value]
    return value


def _validate_ulid(value: str) -> str:
    if not _ULID_PATTERN.fullmatch(value):
        raise ValueError("must be a canonical ULID")
    return value


def _validate_utc_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != _UTC_OFFSET:
        raise ValueError("timestamp must be timezone-aware UTC")
    return value.astimezone(UTC)


def _serialize_timestamp(value: datetime) -> str:
    if value.microsecond == 0:
        value_text = value.isoformat(timespec="seconds")
    elif value.microsecond % 1_000 == 0:
        value_text = value.isoformat(timespec="milliseconds")
    else:
        value_text = value.isoformat(timespec="microseconds")
    return value_text.replace("+00:00", "Z")


class EventModel(BaseModel):
    """Shared immutable behavior for canonical events."""

    model_config = ConfigDict(frozen=True)

    def canonical_json(self) -> str:
        """Return a deterministic JSON representation with sorted object keys."""
        return json.dumps(
            self.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


class TraceEvent(EventModel):
    trace_id: str
    agent_name: str
    agent_version: str
    started_at: datetime
    environment: str
    status: Literal["ok", "error", "partial"] = "ok"
    ended_at: datetime | None = None
    input_ref: str | None = None
    total_tokens: int | None = Field(default=None, ge=0)
    total_cost_usd: float | None = Field(default=None, ge=0)
    latency_ms: float | None = Field(default=None, ge=0)
    attributes: Mapping[str, Any] = Field(default_factory=FrozenDict)

    _validate_trace_id = field_validator("trace_id")(_validate_ulid)
    _validate_started_at = field_validator("started_at")(_validate_utc_timestamp)
    _validate_ended_at = field_validator("ended_at")(_validate_utc_timestamp)
    _freeze_attributes = field_validator("attributes", mode="before")(_deep_freeze)

    @field_serializer("started_at", "ended_at", when_used="json")
    def serialize_timestamp(self, value: datetime | None) -> str | None:
        return None if value is None else _serialize_timestamp(value)

    @field_serializer("attributes", when_used="json")
    def serialize_attributes(self, value: Mapping[str, Any]) -> Any:
        return _json_payload(value)


class SpanEvent(EventModel):
    span_id: str
    trace_id: str
    name: str
    span_kind: Literal["llm", "retrieval", "tool", "compute"]
    started_at: datetime
    parent_span_id: str | None = None
    ended_at: datetime | None = None
    attributes: Mapping[str, Any] = Field(default_factory=FrozenDict)
    prompt_ref: str | None = None
    completion_ref: str | None = None
    model: str | None = None
    temperature: float | None = None
    tokens_in: int | None = Field(default=None, ge=0)
    tokens_out: int | None = Field(default=None, ge=0)
    latency_ms: float | None = Field(default=None, ge=0)

    _validate_span_id = field_validator("span_id")(_validate_ulid)
    _validate_trace_id = field_validator("trace_id")(_validate_ulid)
    _validate_parent_span_id = field_validator("parent_span_id")(_validate_ulid)
    _validate_started_at = field_validator("started_at")(_validate_utc_timestamp)
    _validate_ended_at = field_validator("ended_at")(_validate_utc_timestamp)
    _freeze_attributes = field_validator("attributes", mode="before")(_deep_freeze)

    @field_serializer("started_at", "ended_at", when_used="json")
    def serialize_timestamp(self, value: datetime | None) -> str | None:
        return None if value is None else _serialize_timestamp(value)

    @field_serializer("attributes", when_used="json")
    def serialize_attributes(self, value: Mapping[str, Any]) -> Any:
        return _json_payload(value)


class DecisionEvent(EventModel):
    decision_id: str
    trace_id: str
    agent_name: str
    agent_version: str
    entity_type: str
    entity_id: str
    decision_type: str
    recommendation: Mapping[str, Any]
    rationale: str
    rationale_citations: tuple[str, ...]
    confidence: float = Field(ge=0, le=1)
    alternatives_considered: tuple[Any, ...]
    decided_at: datetime

    _validate_decision_id = field_validator("decision_id")(_validate_ulid)
    _validate_trace_id = field_validator("trace_id")(_validate_ulid)
    _validate_decided_at = field_validator("decided_at")(_validate_utc_timestamp)
    _freeze_recommendation = field_validator("recommendation", mode="before")(_deep_freeze)
    _freeze_alternatives = field_validator("alternatives_considered", mode="before")(_deep_freeze)

    @field_serializer("decided_at", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        return _serialize_timestamp(value)

    @field_serializer("recommendation", "alternatives_considered", when_used="json")
    def serialize_payload(self, value: Any) -> Any:
        return _json_payload(value)


class EvidenceEvent(EventModel):
    evidence_id: str = Field(min_length=1)
    decision_id: str
    source_system: str
    source_ref: str
    field_name: str
    field_value: Any
    weight: float
    retrieved_at: datetime

    _validate_decision_id = field_validator("decision_id")(_validate_ulid)
    _validate_retrieved_at = field_validator("retrieved_at")(_validate_utc_timestamp)
    _freeze_field_value = field_validator("field_value", mode="before")(_deep_freeze)

    @field_serializer("retrieved_at", when_used="json")
    def serialize_timestamp(self, value: datetime) -> str:
        return _serialize_timestamp(value)

    @field_serializer("field_value", when_used="json")
    def serialize_field_value(self, value: Any) -> Any:
        return _json_payload(value)
