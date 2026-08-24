"""Fail-open background persistence for canonical Glassbox events."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from threading import Lock, Thread
from time import monotonic
from typing import Protocol, TypeAlias

from glassbox.events import DecisionEvent, EvidenceEvent, SpanEvent, TraceEvent

from .buffer import Empty, EventBuffer

CanonicalEvent: TypeAlias = TraceEvent | SpanEvent | DecisionEvent | EvidenceEvent


class EventRepository(Protocol):
    """Persistence operations required by :class:`Collector`."""

    def write_event(self, event: CanonicalEvent) -> None:
        """Persist one canonical event."""

    def mark_trace_partial(self, trace_id: str) -> None:
        """Record that an already-persisted trace lost telemetry."""


class Collector:
    """Bounded, non-blocking event admission with a single FIFO writer thread."""

    def __init__(
        self,
        repository: EventRepository,
        *,
        capacity: int = 1_000,
        warning_interval: float = 60.0,
        logger: logging.Logger | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._repository = repository
        self._buffer: EventBuffer[CanonicalEvent] = EventBuffer(capacity=capacity)
        self._warning_interval = warning_interval
        self._logger = logger or logging.getLogger(__name__)
        self._clock = clock
        self._lock = Lock()
        self._accepting = True
        self._closed_traces: set[str] = set()
        self._persisted_traces: set[str] = set()
        self._partial_traces: set[str] = set()
        self._marked_partial_traces: set[str] = set()
        self._decision_traces: dict[str, str] = {}
        self._dropped_events = 0
        self._failed_events = 0
        self._dropped_by_trace: Counter[str] = Counter()
        self._last_warning_at: float | None = None
        # A repository call can block indefinitely. A timed-out shutdown reports
        # failure, while this daemon may finish its best-effort drain later.
        self._thread = Thread(target=self._run, name="glassbox-collector", daemon=True)
        self._thread.start()

    @property
    def dropped_events(self) -> int:
        """Return the number of events rejected because the buffer was full."""
        with self._lock:
            return self._dropped_events

    @property
    def failed_events(self) -> int:
        """Return the number of events the repository rejected in the worker."""
        with self._lock:
            return self._failed_events

    @property
    def dropped_by_trace(self) -> dict[str, int]:
        """Return a snapshot of queue-drop counts keyed by trace identifier."""
        with self._lock:
            return dict(self._dropped_by_trace)

    @property
    def worker_alive(self) -> bool:
        """Return whether the background writer thread is still running."""
        return self._thread.is_alive()

    def emit(self, event: CanonicalEvent) -> bool:
        """Admit *event* without blocking; return whether it was accepted."""
        try:
            with self._lock:
                if not self._accepting:
                    return False
                self._remember_trace(event)
                # Admission and enqueue share shutdown's lifecycle lock. This
                # prevents a post-check enqueue after its worker has exited.
                admitted = self._buffer.put(event)
            if admitted:
                return True
            self._record_loss(event, failed=False)
        except Exception:
            # Telemetry must not change wrapped-agent behavior, including for malformed input.
            return False
        return False

    def flush(self, timeout: float | None = None) -> bool:
        """Wait for admitted writes up to *timeout*, without surfacing failures."""
        try:
            return self._buffer.wait_until_empty(timeout)
        except Exception:
            return False

    def shutdown(self, timeout: float | None = None) -> bool:
        """Stop admission and wait a bounded time for the worker to terminate."""
        try:
            with self._lock:
                self._accepting = False
            deadline = None if timeout is None else monotonic() + timeout
            if not self.flush(self._remaining(deadline)):
                return False
            self._thread.join(self._remaining(deadline))
            return not self._thread.is_alive()
        except Exception:
            return False

    def _run(self) -> None:
        while True:
            try:
                event = self._buffer.get(timeout=0.05)
            except Empty:
                with self._lock:
                    if not self._accepting and self._buffer.empty():
                        return
                continue
            try:
                self._write(event)
            finally:
                self._buffer.task_done()

    def _write(self, event: CanonicalEvent) -> None:
        trace_id = self._trace_id(event)
        try:
            persisted_event = self._partial_event(event)
            self._repository.write_event(persisted_event)
        except Exception:
            self._record_loss(event, failed=True)
            # A failed final write must not depend on a later successful event
            # to update an already-persisted trace.
            self._mark_pending_partials()
            return

        if isinstance(event, TraceEvent):
            with self._lock:
                self._persisted_traces.add(event.trace_id)
                if event.ended_at is not None:
                    self._closed_traces.add(event.trace_id)
                    self._partial_traces.discard(event.trace_id)
                    self._marked_partial_traces.discard(event.trace_id)
        if trace_id is not None:
            self._mark_pending_partials()

    def _partial_event(self, event: CanonicalEvent) -> CanonicalEvent:
        if not isinstance(event, TraceEvent):
            return event
        with self._lock:
            if event.trace_id not in self._partial_traces:
                return event
        return event.model_copy(update={"status": "partial"})

    def _record_loss(self, event: CanonicalEvent, *, failed: bool) -> None:
        trace_id = self._trace_id(event)
        with self._lock:
            if failed:
                self._failed_events += 1
            else:
                self._dropped_events += 1
                if trace_id is not None:
                    self._dropped_by_trace[trace_id] += 1
            if trace_id is not None and trace_id not in self._closed_traces:
                self._partial_traces.add(trace_id)
        message = (
            "repository rejected event" if failed else "collector queue full; dropped newest event"
        )
        self._warn(message)

    def _mark_pending_partials(self) -> None:
        with self._lock:
            trace_ids = tuple(
                self._partial_traces
                & self._persisted_traces
                - self._closed_traces
                - self._marked_partial_traces
            )
        for trace_id in trace_ids:
            try:
                self._repository.mark_trace_partial(trace_id)
            except Exception:
                self._warn("repository rejected partial-trace update")
            else:
                with self._lock:
                    self._marked_partial_traces.add(trace_id)

    def _remember_trace(self, event: CanonicalEvent) -> None:
        if isinstance(event, DecisionEvent):
            self._decision_traces[event.decision_id] = event.trace_id

    def _trace_id(self, event: CanonicalEvent) -> str | None:
        if isinstance(event, EvidenceEvent):
            with self._lock:
                return self._decision_traces.get(event.decision_id)
        return event.trace_id

    def _warn(self, message: str) -> None:
        now = self._clock()
        with self._lock:
            if (
                self._last_warning_at is not None
                and now - self._last_warning_at < self._warning_interval
            ):
                return
            self._last_warning_at = now
        self._logger.warning(message)

    @staticmethod
    def _remaining(deadline: float | None) -> float | None:
        if deadline is None:
            return None
        return max(0.0, deadline - monotonic())


__all__ = ["CanonicalEvent", "Collector", "EventRepository"]
