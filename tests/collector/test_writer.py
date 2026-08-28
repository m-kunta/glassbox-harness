from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
from time import monotonic

from glassbox.collector import Collector
from glassbox.events import DecisionEvent, SpanEvent, TraceEvent
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


def _decision(number: int) -> DecisionEvent:
    return DecisionEvent(
        decision_id=f"01ARZ3NDEKTSV{number:013d}",
        trace_id=TRACE_ID,
        agent_name="replenishment-triage-ai",
        agent_version="abc123",
        entity_type="sku_dc",
        entity_id=f"item-{number}",
        decision_type="flag_exception",
        recommendation={"action": "review"},
        rationale="test",
        rationale_citations=(),
        confidence=0.5,
        alternatives_considered=(),
        decided_at=TIMESTAMP,
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


def test_shutdown_waits_for_an_in_progress_emit_before_the_worker_can_exit(
    monkeypatch: object,
) -> None:
    """An admission that started before shutdown must enqueue before worker exit."""
    repository = RecordingRepository()
    collector = Collector(repository)
    admitted = Event()
    release_admission = Event()
    emit_result: list[bool] = []
    shutdown_result: list[bool] = []
    original_put = collector._buffer.put

    def blocked_put(event: object) -> bool:
        admitted.set()
        assert release_admission.wait(timeout=1)
        return original_put(event)  # type: ignore[arg-type]

    monkeypatch.setattr(collector._buffer, "put", blocked_put)  # type: ignore[union-attr]
    emitter = Thread(target=lambda: emit_result.append(collector.emit(_span(1))))
    emitter.start()
    assert admitted.wait(timeout=1)

    stopper = Thread(target=lambda: shutdown_result.append(collector.shutdown(timeout=1)))
    stopper.start()
    try:
        # The old split check/put path lets shutdown see an empty queue and exit
        # before this paused admission actually reaches the queue.
        Event().wait(timeout=0.1)
        assert collector.worker_alive is True
    finally:
        release_admission.set()
        emitter.join(timeout=1)
        stopper.join(timeout=1)
        collector.shutdown(timeout=1)
    assert emit_result == [True]
    assert shutdown_result == [True]
    assert repository.writes == [_span(1)]


def test_repository_serializes_foreground_operation_with_collector_transaction(
    tmp_path: Path, monkeypatch: object
) -> None:
    """A foreground call cannot join the worker's transaction on a shared connection."""
    repository = Repository(Database.open(tmp_path / "glassbox.sqlite3"))
    repository.write_event(_trace())
    collector = Collector(repository)
    worker_started = Event()
    release_worker = Event()
    foreground_finished = Event()
    original_write_span = repository._write_span

    def blocked_write_span(event: SpanEvent) -> None:
        worker_started.set()
        assert release_worker.wait(timeout=1)
        original_write_span(event)

    monkeypatch.setattr(repository, "_write_span", blocked_write_span)  # type: ignore[union-attr]
    assert collector.emit(_span(1)) is True
    assert worker_started.wait(timeout=1)
    foreground = Thread(
        target=lambda: (repository.mark_trace_partial(TRACE_ID), foreground_finished.set())
    )
    foreground.start()
    try:
        assert foreground_finished.wait(timeout=0.05) is False
    finally:
        release_worker.set()
        foreground.join(timeout=1)
        collector.shutdown(timeout=1)
    assert foreground_finished.is_set()


def test_final_repository_loss_marks_an_already_persisted_trace_partial() -> None:
    repository = RecordingRepository()
    collector = Collector(repository)
    assert collector.emit(_trace()) is True
    assert collector.flush(timeout=1) is True

    repository.fail_writes = True
    assert collector.emit(_span(1)) is True
    assert collector.flush(timeout=1) is True
    assert collector.shutdown(timeout=1) is True

    assert collector.failed_events == 1
    assert repository.partial_traces == [TRACE_ID]


def test_decision_trace_lookup_state_is_bounded_in_a_long_running_process() -> None:
    """_decision_traces maps decision_id -> trace_id purely to correlate a later
    evidence write failure back to its trace for partial-trace bookkeeping.
    Nothing ever removed entries, so a long-lived process accumulating many
    decisions would grow this dict without bound. It must stay bounded."""
    repository = RecordingRepository()
    collector = Collector(repository, capacity=100)

    for number in range(500):
        collector.emit(_decision(number))

    assert len(collector._decision_traces) <= 100
    assert collector.shutdown(timeout=1) is True


def test_timed_out_shutdown_reports_failure_without_a_non_daemon_worker() -> None:
    repository = RecordingRepository()
    repository.block_spans = True
    collector = Collector(repository)
    assert collector.emit(_span(1)) is True
    assert repository.started.wait(timeout=1)

    try:
        assert collector.shutdown(timeout=0.01) is False
        assert collector.worker_alive is True
        assert collector._thread.daemon is True
    finally:
        repository.release.set()
        collector.shutdown(timeout=1)
