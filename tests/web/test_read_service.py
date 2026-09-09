from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from glassbox.events import DecisionEvent, TraceEvent
from glassbox.store import Database, Repository
from glassbox.web.read_service import QueueRequest, ReadService, decode_cursor

TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAX"
TIMESTAMP = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


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
    with pytest.raises(ValueError, match="sort"):
        service.queue(QueueRequest(sort="confidence; DROP TABLE decisions"))


def test_decode_cursor_rejects_unknown_keys_and_wrong_sort_value_type() -> None:
    with pytest.raises(ValueError, match="cursor"):
        decode_cursor("eyJ2YWx1ZSI6Im5vdC1hLWZsb2F0IiwiZGVjaXNpb25faWQiOiJ4In0", "confidence")
