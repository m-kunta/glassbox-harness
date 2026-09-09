"""Per-request orchestration for read-only planner views."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

from glassbox.store import Database, Repository
from glassbox.store.repository import OverrideStatus, QueueCursor, QueueQuery, QueueSort

from .read_models import (
    ConfidenceBand,
    DecisionCard,
    QueuePage,
    QueueRow,
    TraceView,
    build_decision_card,
    build_trace_view,
    confidence_band,
    confidence_bounds,
    recommendation_summary,
)

_SORT_ALIASES: dict[str, QueueSort] = {"timestamp": "decided_at", "confidence": "confidence"}


@dataclass(frozen=True)
class QueueRequest:
    agent_name: str | None = None
    decision_type: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    confidence: str | None = None
    override_status: str | None = None
    sort: str = "timestamp"
    cursor: str | None = None


class ReadService:
    """Open a short-lived strict read-only store for every public operation."""

    def __init__(self, database_path: Path | str) -> None:
        self._database_path = Path(database_path)

    def queue(self, request: QueueRequest) -> QueuePage:
        """Return one parsed, display-ready decision queue page."""
        sort_column = _sort_column(request.sort)
        cursor = decode_cursor(request.cursor, sort_column)
        decided_from, decided_before = _date_bounds(request.date_from, request.date_to)
        confidence_min, confidence_max = _confidence_bounds(request.confidence)
        query = QueueQuery(
            agent_name=request.agent_name,
            decision_type=request.decision_type,
            decided_from=decided_from,
            decided_before=decided_before,
            confidence_min=confidence_min,
            confidence_max=confidence_max,
            override_status=_override_status(request.override_status),
            sort_column=sort_column,
            cursor=cursor,
            limit=26,
        )
        database = Database.open_read_only(self._database_path)
        try:
            decisions = Repository(database).queue(query)
        finally:
            database.close()
        visible = decisions[:25]
        rows = tuple(
            QueueRow(
                decision_id=item.event.decision_id,
                trace_id=item.event.trace_id,
                agent_name=item.event.agent_name,
                decision_type=item.event.decision_type,
                recommendation_summary=recommendation_summary(item.event.model_dump(mode="json")["recommendation"]),
                confidence=item.event.confidence,
                confidence_band=confidence_band(item.event.confidence),
                decided_at=item.event.model_dump(mode="json")["decided_at"],
                override_status=item.override_status,
                sort_value=item.sort_value,
            )
            for item in visible
        )
        next_cursor = (
            encode_cursor(rows[-1], sort_column) if len(decisions) > len(visible) else None
        )
        return QueuePage(rows, next_cursor)

    def decision_card(self, decision_id: str) -> DecisionCard | None:
        """Return one display-ready decision card, if it exists."""
        database = Database.open_read_only(self._database_path)
        try:
            detail = Repository(database).decision_detail(decision_id)
        finally:
            database.close()
        if detail is None:
            return None
        return build_decision_card(detail.stored_decision, detail.overrides)

    def trace(self, trace_id: str) -> TraceView | None:
        """Return one display-ready trace view, if it exists."""
        database = Database.open_read_only(self._database_path)
        try:
            tree = Repository(database).trace_tree(trace_id)
        finally:
            database.close()
        return None if tree is None else build_trace_view(tree)


def encode_cursor(row: QueueRow, sort_column: QueueSort) -> str:
    """Encode a queue row's raw database sort key as an unpadded cursor."""
    value = row.sort_value
    if sort_column == "decided_at" and not isinstance(value, str):
        raise ValueError("timestamp cursor value must be a string")
    if sort_column == "confidence" and (isinstance(value, bool) or not isinstance(value, float)):
        raise ValueError("confidence cursor value must be a float")
    payload = json.dumps(
        {"decision_id": row.decision_id, "value": value}, separators=(",", ":"), sort_keys=True
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_cursor(value: str | None, sort_column: QueueSort) -> QueueCursor | None:
    """Decode a strictly shaped cursor before it reaches a SQL predicate."""
    if value is None:
        return None
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(value + padding))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("invalid queue cursor") from error
    if not isinstance(payload, dict) or set(payload) != {"decision_id", "value"}:
        raise ValueError("invalid queue cursor")
    decision_id = payload["decision_id"]
    sort_value = payload["value"]
    if not isinstance(decision_id, str):
        raise ValueError("invalid queue cursor")
    if sort_column == "decided_at" and not isinstance(sort_value, str):
        raise ValueError("invalid queue cursor")
    if sort_column == "confidence" and (
        isinstance(sort_value, bool) or not isinstance(sort_value, float)
    ):
        raise ValueError("invalid queue cursor")
    return QueueCursor(sort_value, decision_id)


def _sort_column(value: str) -> QueueSort:
    try:
        return _SORT_ALIASES[value]
    except KeyError as error:
        raise ValueError("queue sort is not supported") from error


def _date_bounds(
    value_from: str | None, value_to: str | None
) -> tuple[datetime | None, datetime | None]:
    start = _parse_date(value_from) if value_from is not None else None
    end = _parse_date(value_to) if value_to is not None else None
    if start is not None and end is not None and start > end:
        raise ValueError("queue date range is not ordered")
    decided_from = None if start is None else datetime.combine(start, datetime.min.time(), UTC)
    decided_before = (
        None
        if end is None
        else datetime.combine(end + timedelta(days=1), datetime.min.time(), UTC)
    )
    return (decided_from, decided_before)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError("queue dates must use YYYY-MM-DD") from error


def _confidence_bounds(value: str | None) -> tuple[float | None, float | None]:
    if value is None:
        return (None, None)
    try:
        return confidence_bounds(ConfidenceBand(value))
    except ValueError as error:
        raise ValueError("queue confidence band is not supported") from error


def _override_status(value: str | None) -> OverrideStatus | None:
    if value is None:
        return None
    if value not in {"none", "accepted", "modified", "rejected", "inconsistent"}:
        raise ValueError("queue override status is not supported")
    return cast(OverrideStatus, value)
