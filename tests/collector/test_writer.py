from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from time import monotonic

from glassbox.collector import Collector
from glassbox.events import SpanEvent, TraceEvent
from glassbox.store import Database, Repository

TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
TIMESTAMP = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)


def _trace(*, closed: bool = False) -> TraceEvent:
    values = {
        "trace_id": TRACE_ID,
        "agent_name": "replenishment-triage-ai",
        "agent_version": "abc123",
        "started_at": TIMESTAMP,
        "environment": "shadow",
    }
    if closed:
        values["ended_at"] = TIMESTAMP
    return TraceEvent(**values)


def _span(number: int) -> SpanEvent:
    return SpanEvent(
        span_id=f"01ARZ3NDEKTSV4RRFFQ69G5F{number:02d}",
        trace_id=TRACE_ID,
        name=f"span-{number}",
        span_kind="compute",
        started_at=TIMESTAMP,
    )


class RecordingRepository:
    def __init__(self) -> None:
        self.writes: list[object] = []
        self.partial_traces: list[str] = []
        self.started = Event()
        self.release = Event()
        self.block_spans = False
        self.fail_writes = False

    def write_event(self, event: object) -> None:
        self.started.set()
        if self.block_spans and isinstance(event, SpanEvent):
            self.release.wait()
        if self.fail_writes:
            raise RuntimeError("database unavailable")
        self.writes.append(event)

    def mark_trace_partial(self, trace_id: str) -> None:
        self.partial_traces.append(trace_id)


def test_collector_writes_events_in_fifo_order() -> None:
    repository = RecordingRepository()
    collector = Collector(repository)
    events = (_trace(), _span(1), _span(2))

    assert all(collector.emit(event) for event in events)
    assert collector.flush(timeout=1) is True
    assert repository.writes == list(events)
    assert collector.shutdown(timeout=1) is True
    assert collector.worker_alive is False


def test_collector_persists_through_the_real_repository(tmp_path: Path) -> None:
    repository = Repository(Database.open(tmp_path / "glassbox.sqlite3"))
    collector = Collector(repository)

    assert collector.emit(_trace()) is True
    assert collector.emit(_span(1)) is True
    assert collector.flush(timeout=1) is True
    assert collector.failed_events == 0
    assert repository.trace_tree(TRACE_ID) is not None
    assert collector.shutdown(timeout=1) is True


def test_collector_drops_newest_event_marks_trace_partial_and_counts_loss() -> None:
    repository = RecordingRepository()
    repository.block_spans = True
    collector = Collector(repository, capacity=1)

    assert collector.emit(_trace()) is True
    assert collector.flush(timeout=1) is True
    repository.started.clear()
    assert collector.emit(_span(1)) is True
    assert repository.started.wait(timeout=1)
    assert collector.emit(_span(2)) is True
    assert collector.emit(_span(3)) is False

    assert collector.dropped_events == 1
    assert collector.dropped_by_trace == {TRACE_ID: 1}
    repository.release.set()
    assert collector.flush(timeout=1) is True
    assert repository.partial_traces == [TRACE_ID]
    assert collector.shutdown(timeout=1) is True


def test_collector_rate_limits_queue_drop_warnings(caplog: object) -> None:
    repository = RecordingRepository()
    repository.block_spans = True
    def clock() -> float:
        return 1.0

    logger = logging.getLogger("glassbox.collector.test")
    collector = Collector(repository, capacity=1, warning_interval=60, logger=logger, clock=clock)

    with caplog.at_level(logging.WARNING, logger=logger.name):  # type: ignore[union-attr]
        assert collector.emit(_span(1)) is True
        assert repository.started.wait(timeout=1)
        assert collector.emit(_span(2)) is True
        assert collector.emit(_span(3)) is False
        assert collector.emit(_span(4)) is False

    assert len(caplog.records) == 1  # type: ignore[union-attr]
    repository.release.set()
    assert collector.shutdown(timeout=1) is True


def test_collector_isolates_repository_write_failures() -> None:
    repository = RecordingRepository()
    repository.fail_writes = True
    collector = Collector(repository)

    assert collector.emit(_span(1)) is True
    assert collector.flush(timeout=1) is True
    assert collector.failed_events == 1
    assert collector.shutdown(timeout=1) is True


def test_flush_returns_false_when_a_write_exceeds_its_timeout() -> None:
    repository = RecordingRepository()
    repository.block_spans = True
    collector = Collector(repository)

    assert collector.emit(_span(1)) is True
    assert repository.started.wait(timeout=1)
    started = monotonic()
    assert collector.flush(timeout=0.01) is False
    assert monotonic() - started < 0.2

    repository.release.set()
    assert collector.shutdown(timeout=1) is True


def test_late_loss_after_a_closing_trace_write_only_updates_loss_metrics() -> None:
    repository = RecordingRepository()
    collector = Collector(repository, capacity=1)

    assert collector.emit(_trace(closed=True)) is True
    assert collector.flush(timeout=1) is True
    repository.block_spans = True
    repository.started.clear()
    assert collector.emit(_span(1)) is True
    assert repository.started.wait(timeout=1)
    assert collector.emit(_span(2)) is True
    assert collector.emit(_span(3)) is False

    assert collector.dropped_events == 1
    assert repository.partial_traces == []
    repository.release.set()
    assert collector.shutdown(timeout=1) is True


def test_shutdown_stops_accepting_events_and_terminates_worker() -> None:
    repository = RecordingRepository()
    collector = Collector(repository)

    assert collector.shutdown(timeout=1) is True
    assert collector.emit(_span(1)) is False
    assert collector.worker_alive is False
