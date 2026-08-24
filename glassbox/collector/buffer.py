"""Non-blocking bounded FIFO buffering for collector events."""

from __future__ import annotations

from queue import Empty, Full, Queue
from time import monotonic
from typing import Generic, TypeVar

T = TypeVar("T")


class EventBuffer(Generic[T]):
    """A bounded FIFO queue whose producer path never waits for capacity."""

    def __init__(self, *, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least one")
        self._queue: Queue[T] = Queue(maxsize=capacity)

    def put(self, item: T) -> bool:
        """Add *item*, or return ``False`` when the queue is already full."""
        try:
            self._queue.put_nowait(item)
        except Full:
            return False
        return True

    def get(self, timeout: float | None = None) -> T:
        """Remove and return the oldest buffered item."""
        return self._queue.get(timeout=timeout)

    def task_done(self) -> None:
        """Record completion of one item returned by :meth:`get`."""
        self._queue.task_done()

    def empty(self) -> bool:
        """Return whether no item is currently buffered."""
        return self._queue.empty()

    def wait_until_empty(self, timeout: float | None) -> bool:
        """Wait until every admitted item has been completed, up to *timeout*."""
        deadline = None if timeout is None else monotonic() + timeout
        with self._queue.all_tasks_done:
            while self._queue.unfinished_tasks:
                if deadline is None:
                    self._queue.all_tasks_done.wait()
                    continue
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return False
                self._queue.all_tasks_done.wait(remaining)
        return True


__all__ = ["Empty", "EventBuffer"]
