from __future__ import annotations

from collections.abc import Iterator

import pytest

import glassbox as gb
from glassbox.sdk.config import reset_for_testing


class RecordingCollector:
    def __init__(self) -> None:
        self.events: list[object] = []

    def emit(self, event: object) -> bool:
        self.events.append(event)
        return True

    def flush(self, timeout: float | None = None) -> bool:
        del timeout
        return True

    def shutdown(self, timeout: float | None = None) -> bool:
        del timeout
        return True


@pytest.fixture(autouse=True)
def reset_sdk() -> Iterator[None]:
    yield
    reset_for_testing()


def test_disabled_sdk_performs_zero_writes_and_preserves_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GLASSBOX_ENABLED", "0")
    collector = RecordingCollector()
    gb.init(agent="triage", version="test", env="dev", collector=collector)

    @gb.trace
    def decide() -> str:
        with gb.span("retrieve", kind="retrieval"):
            with gb.decision_context(
                entity_type="sku", entity_id="1", decision_type="flag"
            ) as decision:
                gb.evidence(
                    evidence_id="inventory",
                    source_system="fulfillment",
                    source_ref="item/1",
                    fields={"on_hand": 0},
                )
                decision.complete(
                    recommendation={"action": "review"},
                    rationale="low inventory",
                    rationale_citations=["inventory"],
                    confidence=0.8,
                    alternatives_considered=[],
                )
        return "agent result"

    assert decide() == "agent result"
    assert collector.events == []
    assert gb.flush(timeout=0.01) is True
    assert gb.shutdown(timeout=0.01) is True
