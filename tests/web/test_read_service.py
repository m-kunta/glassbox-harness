from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from glassbox.events import DecisionEvent, TraceEvent
from glassbox.store import Database, Repository
from glassbox.store.repository import OverrideSubmission, QueueCursor
from glassbox.web.read_models import ConfidenceBand, QueueRow, RecommendationView
from glassbox.web.read_service import QueueRequest, ReadService, decode_cursor, encode_cursor

TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAX"
TIMESTAMP = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


def _ulid(seed: int) -> str:
    """Build a valid-format ULID from an integer seed (hex digits only, so the
    Crockford alphabet's excluded letters I/L/O/U never appear)."""
    return "0" + format(seed, "025X")


def _minimal_queue_row(sort_value: object) -> QueueRow:
    return QueueRow(
        decision_id=DECISION_ID,
        trace_id=TRACE_ID,
        agent_name="agent",
        decision_type="replenish",
        entity_label="sku · sku-1",
        recommendation=RecommendationView("order", (), None),
        confidence=0.8,
        confidence_band=ConfidenceBand.HIGH,
        decided_at="2026-09-08T12:00:00Z",
        override_status="none",
        sort_value=sort_value,  # type: ignore[arg-type]
    )


def _strict_database_with_decision(tmp_path: Path) -> Path:
    path = tmp_path / "glassbox.sqlite3"
    database = Database.open(path)
    try:
        repository = Repository(database)
        repository.write_event(
            TraceEvent(
                trace_id=TRACE_ID,
                agent_name="agent",
                agent_version="v1",
                started_at=TIMESTAMP,
                environment="dev",
            )
        )
        repository.write_event(
            DecisionEvent(
                decision_id=DECISION_ID,
                trace_id=TRACE_ID,
                agent_name="agent",
                agent_version="v1",
                entity_type="sku",
                entity_id="sku-1",
                decision_type="replenish",
                recommendation={"action": "order"},
                rationale="Inventory is low.",
                rationale_citations=[],
                confidence=0.8,
                alternatives_considered=[],
                decided_at=TIMESTAMP,
            )
        )
    finally:
        database.close()
    return path


def test_read_service_opens_a_fresh_read_only_database_per_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _strict_database_with_decision(tmp_path)
    service = ReadService(path)
    calls: list[Path] = []
    real_open = Database.open_read_only

    def recording_open(database_path: Path | str, **kwargs: object) -> Database:
        calls.append(Path(database_path))
        return real_open(database_path, **kwargs)

    monkeypatch.setattr(Database, "open_read_only", recording_open)

    assert service.queue(QueueRequest()).rows
    assert service.queue(QueueRequest()).rows
    assert calls == [path, path]


def test_read_service_parses_dates_bands_and_fixed_sort_aliases(tmp_path: Path) -> None:
    service = ReadService(_strict_database_with_decision(tmp_path))

    page = service.queue(
        QueueRequest(
            date_from="2026-09-08",
            date_to="2026-09-08",
            confidence="high",
            sort="confidence",
        )
    )

    assert [row.decision_id for row in page.rows] == [DECISION_ID]
    assert page.rows[0].entity_label == "sku · sku-1"
    with pytest.raises(ValueError, match="sort"):
        service.queue(QueueRequest(sort="confidence; DROP TABLE decisions"))


def test_queue_row_uses_the_same_recommendation_view_as_the_card(tmp_path: Path) -> None:
    service = ReadService(_strict_database_with_decision(tmp_path))

    row = service.queue(QueueRequest()).rows[0]
    card = service.decision_card(DECISION_ID)

    assert card is not None
    assert row.recommendation == card.recommendation
    assert row.recommendation.action == "order"


def test_decode_cursor_rejects_unknown_keys_and_wrong_sort_value_type() -> None:
    with pytest.raises(ValueError, match="cursor"):
        decode_cursor("eyJ2YWx1ZSI6Im5vdC1hLWZsb2F0IiwiZGVjaXNpb25faWQiOiJ4In0", "confidence")


def test_encode_and_decode_cursor_round_trip_the_integer_timestamp_key() -> None:
    """Task 1 changed QueueCursor.sort_value for "decided_at" from raw RFC3339
    text to the parsed glassbox_timestamp_key integer -- this must still
    round-trip through the web layer's own base64/JSON cursor encoding."""
    row = _minimal_queue_row(1_234_567_890)

    encoded = encode_cursor(row, "decided_at")
    decoded = decode_cursor(encoded, "decided_at")

    assert decoded == QueueCursor(1_234_567_890, DECISION_ID)


def test_encode_cursor_rejects_a_non_integer_decided_at_sort_value() -> None:
    row = replace(_minimal_queue_row(0), sort_value="not-an-int")

    with pytest.raises(ValueError, match="integer"):
        encode_cursor(row, "decided_at")


def test_queue_pagination_round_trips_a_decided_at_cursor(tmp_path: Path) -> None:
    """An end-to-end regression check for the same contract change: a real
    queue page beyond the visible limit must produce a next_cursor that,
    fed back into another request, resumes correctly and exhaustively."""
    path = tmp_path / "glassbox.sqlite3"
    decision_ids = [_ulid(0x100 + index) for index in range(26)]
    trace_ids = [_ulid(0x200 + index) for index in range(26)]
    database = Database.open(path)
    try:
        repository = Repository(database)
        for index in range(26):
            decided_at = TIMESTAMP + timedelta(seconds=index)
            repository.write_event(
                TraceEvent(
                    trace_id=trace_ids[index],
                    agent_name="agent",
                    agent_version="v1",
                    started_at=decided_at,
                    environment="dev",
                )
            )
            repository.write_event(
                DecisionEvent(
                    decision_id=decision_ids[index],
                    trace_id=trace_ids[index],
                    agent_name="agent",
                    agent_version="v1",
                    entity_type="sku",
                    entity_id=f"sku-{index}",
                    decision_type="replenish",
                    recommendation={"action": "order"},
                    rationale="Inventory is low.",
                    rationale_citations=[],
                    confidence=0.8,
                    alternatives_considered=[],
                    decided_at=decided_at,
                )
            )
    finally:
        database.close()

    service = ReadService(path)
    first_page = service.queue(QueueRequest())
    assert len(first_page.rows) == 25
    assert first_page.next_cursor is not None
    assert all(isinstance(row.sort_value, int) for row in first_page.rows)

    second_page = service.queue(QueueRequest(cursor=first_page.next_cursor))
    assert second_page.next_cursor is None

    seen_ids = [row.decision_id for row in first_page.rows] + [
        row.decision_id for row in second_page.rows
    ]
    assert seen_ids == list(reversed(decision_ids))


@pytest.mark.parametrize(
    ("supersedes_self", "expected_status"),
    [(False, "accepted"), (True, "inconsistent")],
)
def test_queue_and_card_agree_on_override_status(
    tmp_path: Path, supersedes_self: bool, expected_status: str
) -> None:
    path = _strict_database_with_decision(tmp_path)
    override_id = "01ARZ3NDEKTSV4RRFFQ69G5FAY"
    database = Database.open(path)
    try:
        with database.connection:
            database.connection.execute(
                """
                INSERT INTO overrides (
                    override_id, decision_id, actor, action, modified_value, reason_code,
                    free_text, created_at, supersedes_override_id, idempotency_key
                ) VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?)
                """,
                (
                    override_id,
                    DECISION_ID,
                    "planner",
                    "accepted",
                    "2026-09-08T12:00:00Z",
                    override_id if supersedes_self else None,
                    f"request-{override_id}",
                ),
            )
    finally:
        database.close()

    service = ReadService(path)
    queue_status = service.queue(QueueRequest()).rows[0].override_status
    card = service.decision_card(DECISION_ID)

    assert queue_status == expected_status
    assert card is not None
    assert card.override.status == queue_status


def test_write_path_and_read_views_agree_after_a_superseding_override(tmp_path: Path) -> None:
    path = _strict_database_with_decision(tmp_path)
    database = Database.open(path)
    try:
        repository = Repository(database)
        first = repository.record_override(
            OverrideSubmission(
                "01ARZ3NDEKTSV4RRFFQ69G5FAY",
                DECISION_ID,
                "planner",
                "accepted",
                None,
                None,
                None,
                TIMESTAMP,
                "override-request-one",
            )
        )
        second = repository.record_override(
            OverrideSubmission(
                "01ARZ3NDEKTSV4RRFFQ69G5FAZ",
                DECISION_ID,
                "planner",
                "rejected",
                None,
                None,
                None,
                TIMESTAMP,
                "override-request-two",
            )
        )
    finally:
        database.close()

    service = ReadService(path)
    card = service.decision_card(DECISION_ID)

    assert second.supersedes_override_id == first.override_id
    assert service.queue(QueueRequest()).rows[0].override_status == "rejected"
    assert card is not None
    assert card.override.status == "rejected"
    assert card.override.current == second
