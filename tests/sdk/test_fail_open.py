from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest

import glassbox as gb
from glassbox.sdk.config import reset_for_testing


class FailingCollector:
    def emit(self, event: object) -> bool:
        del event
        raise RuntimeError("collector offline")

    def flush(self, timeout: float | None = None) -> bool:
        del timeout
        raise RuntimeError("collector offline")

    def shutdown(self, timeout: float | None = None) -> bool:
        del timeout
        raise RuntimeError("collector offline")


@pytest.fixture(autouse=True)
def reset_sdk() -> Iterator[None]:
    yield
    reset_for_testing()


def test_collector_failures_never_change_return_values_or_agent_exceptions() -> None:
    gb.init(agent="triage", version="test", env="dev", collector=FailingCollector())

    @gb.trace
    def returns_normally() -> str:
        with gb.span("retrieve", kind="retrieval"):
            return "agent result"

    @gb.trace
    def raises_normally() -> None:
        raise ValueError("agent failure")

    assert returns_normally() == "agent result"
    with pytest.raises(ValueError, match="agent failure"):
        raises_normally()
    assert gb.flush(timeout=0.01) is False
    assert gb.shutdown(timeout=0.01) is False


def test_init_warns_and_never_raises_when_telemetry_setup_is_invalid(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="glassbox.sdk"):
        assert (
            gb.init(
                agent="triage",
                version="test",
                env="dev",
                collector=FailingCollector(),
                redaction_hooks=object(),  # type: ignore[arg-type]
            )
            is None
        )

    assert any("initialization failed" in record.message for record in caplog.records)
