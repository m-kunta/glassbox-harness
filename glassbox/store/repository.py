"""Typed event persistence and trace-tree reads."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from threading import RLock
from typing import Any, TypeAlias, cast

from glassbox.events import DecisionEvent, EvidenceEvent, SpanEvent, TraceEvent
from glassbox.events.models import canonical_dumps

from .database import Database

Event: TypeAlias = TraceEvent | SpanEvent | DecisionEvent | EvidenceEvent

# Fields the closing TraceEvent may omit; a close must not clobber them with
# NULL when it does. Keep this in lockstep with any new optional TraceEvent
# column -- a name missing here silently stops updating on close.
_TRACE_OPTIONAL_COLUMNS = (
    "input_ref",
    "total_tokens",
    "total_cost_usd",
    "latency_ms",
    "attributes",
)


@dataclass(frozen=True)
class StoredDecision:
    """A decision and its decision-scoped evidence citation keys."""

    event: DecisionEvent
    evidence: tuple[EvidenceEvent, ...]


@dataclass(frozen=True)
class TraceTree:
    """The complete persisted trace payload required by a trace export."""

    trace: TraceEvent
    spans: tuple[SpanEvent, ...]
    decisions: tuple[StoredDecision, ...]


class Repository:
    """SQLite persistence for canonical P0 events."""

    def __init__(self, database: Database) -> None:
        self._connection = database.connection
        self._operation_lock: RLock = database._operation_lock

    def write_event(self, event: Event) -> None:
        """Persist one canonical event in a transaction."""
        with self._operation_lock:
            with self._connection:
                if isinstance(event, TraceEvent):
                    self._write_trace(event)
                elif isinstance(event, SpanEvent):
                    self._write_span(event)
                elif isinstance(event, DecisionEvent):
                    self._write_decision(event)
                else:
                    self._write_evidence(event)

    def mark_trace_partial(self, trace_id: str) -> None:
        """Mark an already-persisted trace partial without creating a new trace."""
        with self._operation_lock:
            with self._connection:
                self._connection.execute(
                    "UPDATE traces SET status = 'partial' WHERE trace_id = ?", (trace_id,)
                )

    def trace_tree(self, trace_id: str) -> TraceTree | None:
        """Return the trace, its spans, and decisions with their evidence."""
        with self._operation_lock:
            trace_row = self._connection.execute(
                "SELECT * FROM traces WHERE trace_id = ?", (trace_id,)
            ).fetchone()
            if trace_row is None:
                return None

            spans = tuple(
                self._span_from_row(row)
                for row in self._connection.execute(
                    "SELECT * FROM spans WHERE trace_id = ? ORDER BY started_at, span_id",
                    (trace_id,),
                )
            )
            decisions = tuple(
                self._stored_decision_from_row(row)
                for row in self._connection.execute(
                    "SELECT * FROM decisions WHERE trace_id = ? ORDER BY decided_at, decision_id",
                    (trace_id,),
                )
            )
            return TraceTree(self._trace_from_row(trace_row), spans, decisions)

    def _write_trace(self, event: TraceEvent) -> None:
        payload = event.model_dump(mode="json")
        optional_set_clause = ", ".join(
            f"{column} = CASE WHEN :{column}_is_set THEN excluded.{column} ELSE traces.{column} END"
            for column in _TRACE_OPTIONAL_COLUMNS
        )
        params = payload | {"attributes": self._json(payload["attributes"])}
        for column in _TRACE_OPTIONAL_COLUMNS:
            params[f"{column}_is_set"] = column in event.model_fields_set
        self._connection.execute(
            f"""
            INSERT INTO traces (
                trace_id, agent_name, agent_version, started_at, ended_at, status, environment,
                input_ref, total_tokens, total_cost_usd, latency_ms, attributes
            ) VALUES (
                :trace_id, :agent_name, :agent_version, :started_at, :ended_at, :status,
                :environment,
                :input_ref, :total_tokens, :total_cost_usd, :latency_ms, :attributes
            )
            ON CONFLICT(trace_id) DO UPDATE SET
                ended_at = excluded.ended_at,
                status = excluded.status,
                {optional_set_clause}
            """,
            params,
        )

    def _write_span(self, event: SpanEvent) -> None:
        payload = event.model_dump(mode="json")
        self._connection.execute(
            """
            INSERT INTO spans (
                span_id, trace_id, parent_span_id, name, span_kind, started_at, ended_at,
                attributes,
                prompt_ref, completion_ref, model, temperature, tokens_in, tokens_out, latency_ms
            ) VALUES (
                :span_id, :trace_id, :parent_span_id, :name, :span_kind, :started_at, :ended_at,
                :attributes, :prompt_ref, :completion_ref, :model, :temperature, :tokens_in,
                :tokens_out, :latency_ms
            )
            """,
            payload | {"attributes": self._json(payload["attributes"])},
        )

    def _write_decision(self, event: DecisionEvent) -> None:
        payload = event.model_dump(mode="json")
        self._connection.execute(
            """
            INSERT INTO decisions (
                decision_id, trace_id, agent_name, agent_version, entity_type, entity_id,
                decision_type, recommendation, rationale, rationale_citations, confidence,
                alternatives_considered, decided_at
            ) VALUES (
                :decision_id, :trace_id, :agent_name, :agent_version, :entity_type, :entity_id,
                :decision_type, :recommendation, :rationale, :rationale_citations, :confidence,
                :alternatives_considered, :decided_at
            )
            """,
            payload
            | {
                "recommendation": self._json(payload["recommendation"]),
                "rationale_citations": self._json(payload["rationale_citations"]),
                "alternatives_considered": self._json(payload["alternatives_considered"]),
            },
        )

    def _write_evidence(self, event: EvidenceEvent) -> None:
        payload = event.model_dump(mode="json")
        self._connection.execute(
            """
            INSERT INTO evidence (
                decision_id, evidence_id, source_system, source_ref, field_name,
                field_value_json, weight, retrieved_at
            ) VALUES (
                :decision_id, :evidence_id, :source_system, :source_ref, :field_name,
                :field_value_json, :weight, :retrieved_at
            )
            """,
            payload | {"field_value_json": self._json(payload["field_value"])},
        )

    def _stored_decision_from_row(self, row: sqlite3.Row) -> StoredDecision:
        decision_row = self._row(row)
        evidence = tuple(
            self._evidence_from_row(evidence_row)
            for evidence_row in self._connection.execute(
                """
                SELECT * FROM evidence WHERE decision_id = ?
                ORDER BY retrieved_at, evidence_id, field_name
                """,
                (decision_row["decision_id"],),
            )
        )
        return StoredDecision(self._decision_from_row(row), evidence)

    @staticmethod
    def _trace_from_row(row: sqlite3.Row) -> TraceEvent:
        values = Repository._row(row)
        values["attributes"] = json.loads(values["attributes"])
        return TraceEvent.model_validate(values)

    @staticmethod
    def _span_from_row(row: sqlite3.Row) -> SpanEvent:
        values = Repository._row(row)
        values["attributes"] = json.loads(values["attributes"])
        return SpanEvent.model_validate(values)

    @staticmethod
    def _decision_from_row(row: sqlite3.Row) -> DecisionEvent:
        values = Repository._row(row)
        for field in ("recommendation", "rationale_citations", "alternatives_considered"):
            values[field] = json.loads(values[field])
        return DecisionEvent.model_validate(values)

    @staticmethod
    def _evidence_from_row(row: sqlite3.Row) -> EvidenceEvent:
        values = Repository._row(row)
        values["field_value"] = json.loads(values.pop("field_value_json"))
        return EvidenceEvent.model_validate(values)

    @staticmethod
    def _json(value: object) -> str:
        return canonical_dumps(value)

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        values = cast(dict[str, Any], dict(row))
        return {key: value for key, value in values.items() if value is not None}
