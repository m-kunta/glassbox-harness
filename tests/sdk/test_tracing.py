from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

import glassbox as gb
from glassbox.collector import Collector
from glassbox.events import DecisionEvent, EvidenceEvent, SpanEvent, TraceEvent
from glassbox.sdk.config import reset_for_testing
from glassbox.store import Database, Repository


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
def configured_collector(monkeypatch: pytest.MonkeyPatch) -> Iterator[RecordingCollector]:
    monkeypatch.delenv("GLASSBOX_ENABLED", raising=False)
    collector = RecordingCollector()
    gb.init(agent="triage", version="test", env="dev", collector=collector)
    yield collector
    reset_for_testing()


def test_nested_sync_traces_share_one_trace_and_spans_keep_parentage(
    configured_collector: RecordingCollector,
) -> None:
    @gb.trace
    def child() -> str:
        with gb.span("child", kind="compute"):
            return "child-result"

    @gb.trace
    def parent() -> str:
        with gb.span("parent", kind="compute"):
            return child()

    assert parent() == "child-result"

    traces = [event for event in configured_collector.events if isinstance(event, TraceEvent)]
    spans = [event for event in configured_collector.events if isinstance(event, SpanEvent)]
    assert len(traces) == 2
    assert traces[0].trace_id == traces[1].trace_id
    assert traces[0].ended_at is None
    assert traces[1].ended_at is not None
    assert [span.name for span in spans] == ["child", "parent"]
    assert spans[0].parent_span_id == spans[1].span_id
    assert all(span.trace_id == traces[0].trace_id for span in spans)


def test_nested_async_traces_propagate_context_without_changing_return_value(
    configured_collector: RecordingCollector,
) -> None:
    @gb.trace
    async def child() -> str:
        await asyncio.sleep(0)
        with gb.span("child", kind="compute"):
            return "async-result"

    @gb.trace
    async def parent() -> str:
        return await child()

    assert asyncio.run(parent()) == "async-result"
    traces = [event for event in configured_collector.events if isinstance(event, TraceEvent)]
    spans = [event for event in configured_collector.events if isinstance(event, SpanEvent)]
    assert len(traces) == 2
    assert traces[0].trace_id == spans[0].trace_id == traces[1].trace_id


def test_parallel_async_tasks_keep_trace_contexts_isolated(
    configured_collector: RecordingCollector,
) -> None:
    @gb.trace
    async def traced(name: str) -> str:
        await asyncio.sleep(0)
        with gb.span(name, kind="compute"):
            await asyncio.sleep(0)
        return name

    async def run_parallel() -> list[str]:
        return await asyncio.gather(traced("first"), traced("second"))

    assert asyncio.run(run_parallel()) == ["first", "second"]

    spans = [event for event in configured_collector.events if isinstance(event, SpanEvent)]
    assert {span.name for span in spans} == {"first", "second"}
    assert len({span.trace_id for span in spans}) == 2


def test_decision_owns_all_evidence_until_it_is_completed(
    configured_collector: RecordingCollector,
) -> None:
    @gb.trace
    def decide() -> None:
        with gb.decision_context(
            entity_type="sku_dc", entity_id="sku-1", decision_type="flag_exception"
        ) as decision:
            gb.evidence(
                evidence_id="inventory",
                source_system="fulfillment",
                source_ref="item/sku-1",
                fields={"on_hand": 0, "lead_time_days": 14},
                weight=0.8,
            )
            decision.complete(
                recommendation={"action": "review"},
                rationale="Inventory is low.",
                rationale_citations=["inventory"],
                confidence=0.8,
                alternatives_considered=[{"action": "ignore"}],
            )

    decide()

    decision = next(
        event for event in configured_collector.events if isinstance(event, DecisionEvent)
    )
    evidence = [event for event in configured_collector.events if isinstance(event, EvidenceEvent)]
    assert len(evidence) == 2
    assert {event.field_name for event in evidence} == {"on_hand", "lead_time_days"}
    assert all(event.decision_id == decision.decision_id for event in evidence)
    assert all(event.weight == 0.8 for event in evidence)
    assert configured_collector.events.index(decision) < configured_collector.events.index(
        evidence[0]
    )


def test_nested_decision_contexts_are_rejected(configured_collector: RecordingCollector) -> None:
    @gb.trace
    def decide() -> None:
        with gb.decision_context(entity_type="sku", entity_id="1", decision_type="flag"):
            with gb.decision_context(entity_type="sku", entity_id="2", decision_type="flag"):
                pass

    with pytest.raises(RuntimeError, match="nested decision"):
        decide()
    assert not [event for event in configured_collector.events if isinstance(event, DecisionEvent)]


def test_incomplete_decision_discards_evidence_and_rate_limits_warning(
    configured_collector: RecordingCollector, caplog: pytest.LogCaptureFixture
) -> None:
    @gb.trace
    def decide() -> None:
        for entity_id in ("1", "2"):
            with gb.decision_context(entity_type="sku", entity_id=entity_id, decision_type="flag"):
                gb.evidence(
                    evidence_id="inventory",
                    source_system="fulfillment",
                    source_ref="item",
                    fields={"on_hand": 0},
                )

    with caplog.at_level(logging.WARNING, logger="glassbox.sdk"):
        decide()

    assert not [event for event in configured_collector.events if isinstance(event, DecisionEvent)]
    assert not [event for event in configured_collector.events if isinstance(event, EvidenceEvent)]
    assert sum("incomplete decision" in record.message for record in caplog.records) == 1


def test_redaction_runs_before_evidence_is_emitted(
    configured_collector: RecordingCollector,
) -> None:
    gb.init(
        agent="triage",
        version="test",
        env="dev",
        collector=configured_collector,
        redaction_hooks=(
            lambda value: (
                value.replace("secret", "[redacted]") if isinstance(value, str) else value
            ),
        ),
    )

    @gb.trace
    def decide() -> None:
        with gb.decision_context(
            entity_type="sku", entity_id="1", decision_type="flag"
        ) as decision:
            gb.evidence(
                evidence_id="inventory",
                source_system="fulfillment",
                source_ref="item/secret",
                fields={"note": "secret"},
            )
            decision.complete(
                recommendation={"note": "secret"},
                rationale="secret",
                rationale_citations=["inventory"],
                confidence=0.5,
                alternatives_considered=[],
            )

    decide()

    decision = next(
        event for event in configured_collector.events if isinstance(event, DecisionEvent)
    )
    evidence = next(
        event for event in configured_collector.events if isinstance(event, EvidenceEvent)
    )
    assert decision.recommendation == {"note": "[redacted]"}
    assert decision.rationale == "[redacted]"
    assert evidence.source_ref == "item/[redacted]"
    assert evidence.field_value == "[redacted]"


def test_sdk_persists_a_completed_trace_tree_through_the_collector(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)
    collector = Collector(repository)
    gb.init(agent="triage", version="test", env="dev", collector=collector)

    @gb.trace
    def decide() -> None:
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
                    rationale="inventory is low",
                    rationale_citations=["inventory"],
                    confidence=0.8,
                    alternatives_considered=[],
                )

    decide()
    assert gb.flush(timeout=1) is True
    assert collector.shutdown(timeout=1) is True

    trace_id = database.connection.execute("SELECT trace_id FROM traces").fetchone()[0]
    tree = repository.trace_tree(trace_id)
    assert tree is not None
    assert tree.trace.ended_at is not None
    assert len(tree.spans) == 1
    assert len(tree.decisions) == 1
    assert tree.decisions[0].evidence[0].evidence_id == "inventory"
