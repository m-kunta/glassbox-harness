"""Trace completion timing at the SDK's public instrumentation boundary."""

from datetime import UTC, datetime, timedelta

import pytest

import glassbox as gb
from glassbox.events import TraceEvent
from glassbox.sdk import tracer
from glassbox.sdk.config import reset_for_testing


class RecordingCollector:
    def __init__(self) -> None:
        self.events: list[object] = []

    def emit(self, event: object) -> bool:
        self.events.append(event)
        return True

    def flush(self, timeout: float | None = None) -> bool:
        return True

    def shutdown(self, timeout: float | None = None) -> bool:
        return True


@pytest.mark.parametrize("fails", [False, True])
def test_completed_trace_latency_uses_persisted_timestamps(monkeypatch, fails):
    start = datetime(2026, 10, 10, tzinfo=UTC)
    end = start + timedelta(milliseconds=250)
    instants = iter((start, end))
    monkeypatch.setattr(tracer, "_now", lambda: next(instants))
    monkeypatch.delenv("GLASSBOX_ENABLED", raising=False)
    collector = RecordingCollector()
    gb.init(agent="test", version="test", collector=collector)

    @gb.trace
    def workload():
        if fails:
            raise RuntimeError("workload failed")
        return "result"

    try:
        if fails:
            with pytest.raises(RuntimeError, match="workload failed"):
                workload()
        else:
            assert workload() == "result"
        initial, completed = [event for event in collector.events if isinstance(event, TraceEvent)]
        assert initial.started_at == start
        assert initial.ended_at is None
        assert initial.latency_ms is None
        assert completed.ended_at == end
        assert completed.latency_ms == 250.0
        assert completed.status == ("error" if fails else "ok")
    finally:
        reset_for_testing()
