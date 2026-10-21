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


def test_explicit_trace_cost_is_emitted_only_when_the_trace_completes(monkeypatch):
    start = datetime(2026, 10, 20, tzinfo=UTC)
    end = start + timedelta(milliseconds=1)
    instants = iter((start, end))
    monkeypatch.setattr(tracer, "_now", lambda: next(instants))
    monkeypatch.delenv("GLASSBOX_ENABLED", raising=False)
    collector = RecordingCollector()
    gb.init(agent="test", version="test", collector=collector)

    @gb.trace
    def workload() -> None:
        gb.set_trace_cost_usd(0.0125)
        assert len(collector.events) == 1

    try:
        workload()
        initial, completed = [event for event in collector.events if isinstance(event, TraceEvent)]
        assert initial.total_cost_usd is None
        assert completed.total_cost_usd == 0.0125
    finally:
        reset_for_testing()


def test_explicit_trace_cost_replaces_the_previous_total(monkeypatch):
    monkeypatch.delenv("GLASSBOX_ENABLED", raising=False)
    collector = RecordingCollector()
    gb.init(agent="test", version="test", collector=collector)

    @gb.trace
    def workload() -> None:
        gb.set_trace_cost_usd(0.01)
        gb.set_trace_cost_usd(0.02)

    try:
        workload()
        initial, completed = [event for event in collector.events if isinstance(event, TraceEvent)]
        assert initial.total_cost_usd is None
        assert completed.total_cost_usd == 0.02
    finally:
        reset_for_testing()


@pytest.mark.parametrize("invalid_cost", [-0.01, float("nan"), float("inf"), "0.01", True])
def test_invalid_trace_cost_is_a_no_op(monkeypatch, invalid_cost):
    monkeypatch.delenv("GLASSBOX_ENABLED", raising=False)
    collector = RecordingCollector()
    gb.init(agent="test", version="test", collector=collector)

    @gb.trace
    def workload() -> None:
        gb.set_trace_cost_usd(invalid_cost)

    try:
        workload()
        initial, completed = [event for event in collector.events if isinstance(event, TraceEvent)]
        assert initial.total_cost_usd is None
        assert completed.total_cost_usd is None
    finally:
        reset_for_testing()


def test_trace_cost_without_an_active_trace_is_a_no_op(monkeypatch):
    monkeypatch.delenv("GLASSBOX_ENABLED", raising=False)
    collector = RecordingCollector()
    gb.init(agent="test", version="test", collector=collector)
    try:
        gb.set_trace_cost_usd(0.01)
        assert collector.events == []
    finally:
        reset_for_testing()


def test_trace_cost_with_disabled_tracing_is_a_no_op(monkeypatch):
    monkeypatch.setenv("GLASSBOX_ENABLED", "0")
    collector = RecordingCollector()
    gb.init(agent="test", version="test", collector=collector)

    @gb.trace
    def workload() -> None:
        gb.set_trace_cost_usd(0.01)

    try:
        workload()
        assert collector.events == []
    finally:
        reset_for_testing()


def test_cost_helper_is_reexported_from_both_public_surfaces():
    assert gb.set_trace_cost_usd is tracer.set_trace_cost_usd
    from glassbox import sdk

    assert sdk.set_trace_cost_usd is tracer.set_trace_cost_usd


def test_explicit_trace_cost_is_retained_when_the_workload_raises(monkeypatch):
    monkeypatch.delenv("GLASSBOX_ENABLED", raising=False)
    collector = RecordingCollector()
    gb.init(agent="test", version="test", collector=collector)
    original_error = RuntimeError("workload failed")

    @gb.trace
    def workload() -> None:
        gb.set_trace_cost_usd(0.0125)
        raise original_error

    try:
        with pytest.raises(RuntimeError, match="workload failed") as raised:
            workload()
        assert raised.value is original_error
        initial, completed = [event for event in collector.events if isinstance(event, TraceEvent)]
        assert initial.total_cost_usd is None
        assert completed.total_cost_usd == 0.0125
        assert completed.status == "error"
    finally:
        reset_for_testing()
