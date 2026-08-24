from __future__ import annotations

from glassbox.collector.buffer import EventBuffer


def test_buffer_preserves_fifo_order_and_drops_newest_when_full() -> None:
    buffer: EventBuffer[str] = EventBuffer(capacity=2)

    assert buffer.put("first") is True
    assert buffer.put("second") is True
    assert buffer.put("third") is False

    assert buffer.get() == "first"
    buffer.task_done()
    assert buffer.get() == "second"
    buffer.task_done()
    assert buffer.wait_until_empty(timeout=0.01) is True
