"""Typed event persistence and trace-tree reads."""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Any, Literal, TypeAlias, cast

from glassbox.events import DecisionEvent, EvidenceEvent, SpanEvent, TraceEvent
from glassbox.events.models import canonical_dumps

from .database import Database

Event: TypeAlias = TraceEvent | SpanEvent | DecisionEvent | EvidenceEvent
QueueSort: TypeAlias = Literal["decided_at", "confidence"]
OverrideStatus: TypeAlias = Literal["none", "accepted", "modified", "rejected", "inconsistent"]
FeedbackVerdict: TypeAlias = Literal["agree", "disagree", "uncertain"]

_SORT_COLUMNS: dict[QueueSort, str] = {
    "decided_at": "d.decided_at",
    "confidence": "d.confidence",
}
_OVERRIDE_STATUSES = frozenset(("none", "accepted", "modified", "rejected", "inconsistent"))
_UTC_OFFSET = timedelta(0)

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


@dataclass(frozen=True)
class QueueCursor:
    """A stable boundary for one descending queue sort order."""

    sort_value: str | float
    decision_id: str


@dataclass(frozen=True)
class QueueQuery:
    """Bound filters and a fixed sort choice for the decision queue."""

    agent_name: str | None = None
    decision_type: str | None = None
    decided_from: datetime | None = None
    decided_before: datetime | None = None
    confidence_min: float | None = None
    confidence_max: float | None = None
    override_status: OverrideStatus | None = None
    sort_column: QueueSort = "decided_at"
    cursor: QueueCursor | None = None
    limit: int = 25


@dataclass(frozen=True)
class OverrideRecord:
    """A persisted override row for read-only display."""

    override_id: str
    decision_id: str
    actor: str
    action: Literal["accepted", "modified", "rejected"]
    modified_value: Any | None
    reason_code: str | None
    free_text: str | None
    created_at: datetime
    supersedes_override_id: str | None


@dataclass(frozen=True)
class QueueDecision:
    """A decision plus its effective override status and raw queue key."""

    event: DecisionEvent
    override_status: OverrideStatus
    sort_value: str | float


@dataclass(frozen=True)
class DecisionDetail:
    """A complete decision with its persisted override history."""

    stored_decision: StoredDecision
    overrides: tuple[OverrideRecord, ...]


@dataclass(frozen=True)
class FeedbackSubmission:
    """An immutable, caller-complete feedback write request."""

    feedback_id: str
    decision_id: str
    verdict: FeedbackVerdict
    reason_code: str | None
    free_text: str | None
    corrected_recommendation: Any | None
    created_at: datetime
    idempotency_key: str


@dataclass(frozen=True)
class FeedbackRecord:
    """One append-only planner assessment stored for a decision."""

    feedback_id: str
    decision_id: str
    verdict: FeedbackVerdict
    reason_code: str | None
    free_text: str | None
    corrected_recommendation: Any | None
    created_at: datetime
    idempotency_key: str


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

    def queue(self, query: QueueQuery) -> tuple[QueueDecision, ...]:
        """Return decisions matching *query* with a stable, fixed-column order."""
        self._validate_queue_query(query)
        sort_column = _SORT_COLUMNS[query.sort_column]
        conditions: list[str] = []
        params: dict[str, Any] = {"limit": query.limit}

        if query.agent_name is not None:
            conditions.append("d.agent_name = :agent_name")
            params["agent_name"] = query.agent_name
        if query.decision_type is not None:
            conditions.append("d.decision_type = :decision_type")
            params["decision_type"] = query.decision_type
        if query.decided_from is not None:
            conditions.append("d.decided_at >= :decided_from")
            params["decided_from"] = self._timestamp_text(query.decided_from)
        if query.decided_before is not None:
            conditions.append("d.decided_at < :decided_before")
            params["decided_before"] = self._timestamp_text(query.decided_before)
        if query.confidence_min is not None:
            conditions.append("d.confidence >= :confidence_min")
            params["confidence_min"] = query.confidence_min
        if query.confidence_max is not None:
            conditions.append("d.confidence < :confidence_max")
            params["confidence_max"] = query.confidence_max
        if query.override_status is not None:
            conditions.append("state.override_status = :override_status")
            params["override_status"] = query.override_status
        if query.cursor is not None:
            conditions.append(
                f"({sort_column} < :cursor_value OR "
                f"({sort_column} = :cursor_value AND d.decision_id < :cursor_decision_id))"
            )
            params["cursor_value"] = query.cursor.sort_value
            params["cursor_decision_id"] = query.cursor.decision_id

        where_clause = " AND ".join(conditions) if conditions else "1 = 1"
        with self._operation_lock:
            rows = self._connection.execute(
                f"""
                WITH override_state AS (
                    SELECT
                        d.decision_id,
                        CASE
                            WHEN COUNT(o.override_id) = 0 THEN 'none'
                            WHEN SUM(
                                CASE WHEN o.override_id IS NOT NULL AND NOT EXISTS (
                                    SELECT 1 FROM overrides AS child
                                    WHERE child.supersedes_override_id = o.override_id
                                ) THEN 1 ELSE 0 END
                            ) = 1 THEN MAX(
                                CASE WHEN o.override_id IS NOT NULL AND NOT EXISTS (
                                    SELECT 1 FROM overrides AS child
                                    WHERE child.supersedes_override_id = o.override_id
                                ) THEN o.action END
                            )
                            ELSE 'inconsistent'
                        END AS override_status
                    FROM decisions AS d
                    LEFT JOIN overrides AS o ON o.decision_id = d.decision_id
                    GROUP BY d.decision_id
                )
                SELECT d.*, state.override_status, {sort_column} AS sort_value
                FROM decisions AS d
                JOIN override_state AS state ON state.decision_id = d.decision_id
                WHERE {where_clause}
                ORDER BY {sort_column} DESC, d.decision_id DESC
                LIMIT :limit
                """,
                params,
            ).fetchall()
            return tuple(self._queue_decision_from_row(row) for row in rows)

    def decision_detail(self, decision_id: str) -> DecisionDetail | None:
        """Return one persisted decision and all of its override records."""
        with self._operation_lock:
            row = self._connection.execute(
                "SELECT * FROM decisions WHERE decision_id = ?", (decision_id,)
            ).fetchone()
            if row is None:
                return None
            stored_decision = self._stored_decision_from_row(row)
            overrides = tuple(
                self._override_from_row(override_row)
                for override_row in self._connection.execute(
                    """
                    SELECT * FROM overrides WHERE decision_id = ?
                    ORDER BY created_at, override_id
                    """,
                    (decision_id,),
                )
            )
            return DecisionDetail(stored_decision, overrides)

    def record_feedback(self, submission: FeedbackSubmission) -> FeedbackRecord:
        """Append feedback, or return the original row for an exact replay."""
        self._validate_feedback_submission(submission)
        corrected_recommendation = (
            None
            if submission.corrected_recommendation is None
            else self._json(submission.corrected_recommendation)
        )
        created_at = self._timestamp_text(submission.created_at)
        with self._operation_lock:
            with self._connection:
                existing_row = self._connection.execute(
                    "SELECT * FROM feedback WHERE idempotency_key = ?",
                    (submission.idempotency_key,),
                ).fetchone()
                if existing_row is not None:
                    existing = self._feedback_from_row(existing_row)
                    if (
                        existing.decision_id == submission.decision_id
                        and existing.verdict == submission.verdict
                        and existing.reason_code == submission.reason_code
                        and existing.free_text == submission.free_text
                        and existing.corrected_recommendation == submission.corrected_recommendation
                    ):
                        return existing
                    raise ValueError("feedback idempotency key was reused with a different payload")
                self._connection.execute(
                    """
                    INSERT INTO feedback (
                        feedback_id, decision_id, verdict, reason_code, free_text,
                        corrected_recommendation, created_at, idempotency_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        submission.feedback_id,
                        submission.decision_id,
                        submission.verdict,
                        submission.reason_code,
                        submission.free_text,
                        corrected_recommendation,
                        created_at,
                        submission.idempotency_key,
                    ),
                )
                row = self._connection.execute(
                    "SELECT * FROM feedback WHERE feedback_id = ?", (submission.feedback_id,)
                ).fetchone()
                assert row is not None
                return self._feedback_from_row(row)

    def feedback_for_decision(self, decision_id: str) -> tuple[FeedbackRecord, ...]:
        """Return feedback newest first for one existing or missing decision."""
        with self._operation_lock:
            return tuple(
                self._feedback_from_row(row)
                for row in self._connection.execute(
                    """
                    SELECT * FROM feedback WHERE decision_id = ?
                    ORDER BY created_at DESC, feedback_id DESC
                    """,
                    (decision_id,),
                )
            )

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
    def _queue_decision_from_row(row: sqlite3.Row) -> QueueDecision:
        status = row["override_status"]
        sort_value = row["sort_value"]
        if status not in _OVERRIDE_STATUSES:
            raise RuntimeError("database returned an invalid override status")
        if not isinstance(sort_value, (str, float)):
            raise RuntimeError("database returned an invalid queue sort value")
        return QueueDecision(
            Repository._decision_from_row(row),
            cast(OverrideStatus, status),
            sort_value,
        )

    @staticmethod
    def _override_from_row(row: sqlite3.Row) -> OverrideRecord:
        values = dict(row)
        modified_value = values["modified_value"]
        return OverrideRecord(
            override_id=values["override_id"],
            decision_id=values["decision_id"],
            actor=values["actor"],
            action=cast(Literal["accepted", "modified", "rejected"], values["action"]),
            modified_value=None if modified_value is None else json.loads(modified_value),
            reason_code=values["reason_code"],
            free_text=values["free_text"],
            created_at=datetime.fromisoformat(values["created_at"].replace("Z", "+00:00")),
            supersedes_override_id=values["supersedes_override_id"],
        )

    @staticmethod
    def _feedback_from_row(row: sqlite3.Row) -> FeedbackRecord:
        values = dict(row)
        corrected_recommendation = values["corrected_recommendation"]
        return FeedbackRecord(
            feedback_id=values["feedback_id"],
            decision_id=values["decision_id"],
            verdict=cast(FeedbackVerdict, values["verdict"]),
            reason_code=values["reason_code"],
            free_text=values["free_text"],
            corrected_recommendation=(
                None if corrected_recommendation is None else json.loads(corrected_recommendation)
            ),
            created_at=datetime.fromisoformat(values["created_at"].replace("Z", "+00:00")),
            idempotency_key=values["idempotency_key"],
        )

    @staticmethod
    def _validate_feedback_submission(submission: FeedbackSubmission) -> None:
        if submission.verdict not in {"agree", "disagree", "uncertain"}:
            raise ValueError("feedback verdict is not supported")
        if not (
            submission.feedback_id and submission.decision_id and submission.idempotency_key
        ):
            raise ValueError("feedback identifiers must be non-empty")
        if submission.created_at.tzinfo is None or submission.created_at.utcoffset() != _UTC_OFFSET:
            raise ValueError("feedback timestamp must be timezone-aware UTC")
        for value in (submission.reason_code, submission.free_text):
            if value is not None and not isinstance(value, str):
                raise ValueError("feedback text values must be strings")

    @staticmethod
    def _validate_queue_query(query: QueueQuery) -> None:
        if not 1 <= query.limit <= 100:
            raise ValueError("queue limit must be between 1 and 100")
        if query.sort_column not in _SORT_COLUMNS:
            raise ValueError("queue sort column is not supported")
        if query.override_status is not None and query.override_status not in _OVERRIDE_STATUSES:
            raise ValueError("queue override status is not supported")
        for bound in (query.confidence_min, query.confidence_max):
            if bound is not None and (
                isinstance(bound, bool)
                or not isinstance(bound, (int, float))
                or not math.isfinite(bound)
                or not 0 <= bound <= 1
            ):
                raise ValueError("queue confidence bounds must be finite values between 0 and 1")
        if (
            query.confidence_min is not None
            and query.confidence_max is not None
            and query.confidence_min > query.confidence_max
        ):
            raise ValueError("queue confidence bounds must be ordered")
        if query.cursor is not None:
            if not isinstance(query.cursor.decision_id, str):
                raise ValueError("queue cursor decision ID must be a string")
            if query.sort_column == "decided_at" and not isinstance(query.cursor.sort_value, str):
                raise ValueError("timestamp queue cursor value must be a string")
            if query.sort_column == "confidence" and (
                isinstance(query.cursor.sort_value, bool)
                or not isinstance(query.cursor.sort_value, float)
            ):
                raise ValueError("confidence queue cursor value must be a float")

    @staticmethod
    def _timestamp_text(value: datetime) -> str:
        if value.tzinfo is None or value.utcoffset() != _UTC_OFFSET:
            raise ValueError("queue timestamp bounds must be timezone-aware UTC")
        value = value.astimezone(UTC)
        if value.microsecond == 0:
            value_text = value.isoformat(timespec="seconds")
        elif value.microsecond % 1_000 == 0:
            value_text = value.isoformat(timespec="milliseconds")
        else:
            value_text = value.isoformat(timespec="microseconds")
        return value_text.replace("+00:00", "Z")

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
