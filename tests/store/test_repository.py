from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from glassbox.events import DecisionEvent, EvidenceEvent, SpanEvent, TraceEvent
from glassbox.store import Database, Repository

TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
SPAN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAX"
TIMESTAMP = datetime(2026, 8, 22, 14, 30, 45, 123000, tzinfo=UTC)


def _events() -> tuple[TraceEvent, SpanEvent, DecisionEvent, EvidenceEvent]:
    trace = TraceEvent(
        trace_id=TRACE_ID,
        agent_name="replenishment-triage-ai",
        agent_version="abc123",
        started_at=TIMESTAMP,
        environment="shadow",
        attributes={"batch": 4, "dry_run": False, "unset": None},
    )
    span = SpanEvent(
        span_id=SPAN_ID,
        trace_id=TRACE_ID,
        name="retrieve_context",
        span_kind="retrieval",
        started_at=TIMESTAMP,
        attributes={"filters": ["inventory", 7]},
    )
    decision = DecisionEvent(
        decision_id=DECISION_ID,
        trace_id=TRACE_ID,
        agent_name="replenishment-triage-ai",
        agent_version="abc123",
        entity_type="sku_dc",
        entity_id="123-DC04",
        decision_type="flag_exception",
        recommendation={"action": "review", "threshold": 0.25},
        rationale="Inventory risk is elevated.",
        rationale_citations=["inventory_position"],
        confidence=0.8,
        alternatives_considered=[{"action": "ignore", "reason": "not enough risk"}],
        decided_at=TIMESTAMP,
    )
    evidence = EvidenceEvent(
        evidence_id="inventory_position",
        decision_id=DECISION_ID,
        source_system="BY_Fulfillment",
        source_ref="item_loc/123/DC04",
        field_name="on_hand",
        field_value={"units": 0, "is_estimated": False, "notes": None},
        weight=0.8,
        retrieved_at=TIMESTAMP,
    )
    return trace, span, decision, evidence


def test_write_event_round_trips_typed_json_in_trace_tree(tmp_path: Path) -> None:
    repository = Repository(Database.open(tmp_path / "glassbox.sqlite3"))
    trace, span, decision, evidence = _events()

    for event in (trace, span, decision, evidence):
        repository.write_event(event)

    tree = repository.trace_tree(TRACE_ID)

    assert tree is not None
    assert tree.trace == trace
    assert tree.spans == (span,)
    assert tree.decisions[0].event == decision
    assert tree.decisions[0].evidence == (evidence,)
    assert tree.trace.attributes == {"batch": 4, "dry_run": False, "unset": None}
    assert tree.decisions[0].event.recommendation == {"action": "review", "threshold": 0.25}
    assert tree.decisions[0].evidence[0].field_value == {
        "units": 0,
        "is_estimated": False,
        "notes": None,
    }


def test_write_event_is_atomic_when_database_rejects_a_foreign_key(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)
    _, span, _, _ = _events()

    with pytest.raises(sqlite3.IntegrityError):
        repository.write_event(span)

    assert database.connection.execute("SELECT COUNT(*) FROM spans").fetchone()[0] == 0


def test_duplicate_caller_evidence_key_is_rejected_without_replacing_prior_evidence(
    tmp_path: Path,
) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)
    trace, _, decision, evidence = _events()
    repository.write_event(trace)
    repository.write_event(decision)
    repository.write_event(evidence)

    with pytest.raises(sqlite3.IntegrityError):
        repository.write_event(evidence)

    tree = repository.trace_tree(TRACE_ID)
    assert tree is not None
    assert tree.decisions[0].evidence == (evidence,)


def test_mark_trace_partial_updates_an_existing_trace(tmp_path: Path) -> None:
    repository = Repository(Database.open(tmp_path / "glassbox.sqlite3"))
    trace, _, _, _ = _events()
    repository.write_event(trace)

    repository.mark_trace_partial(TRACE_ID)

    tree = repository.trace_tree(TRACE_ID)
    assert tree is not None
    assert tree.trace.status == "partial"


def test_mark_trace_partial_leaves_missing_trace_uncreated(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)

    repository.mark_trace_partial(TRACE_ID)

    assert repository.trace_tree(TRACE_ID) is None
    assert database.connection.execute("SELECT COUNT(*) FROM traces").fetchone()[0] == 0


def test_final_trace_event_updates_the_open_trace_without_losing_children(tmp_path: Path) -> None:
    repository = Repository(Database.open(tmp_path / "glassbox.sqlite3"))
    trace, span, _, _ = _events()
    final_trace = trace.model_copy(update={"ended_at": TIMESTAMP, "status": "error"})

    repository.write_event(trace)
    repository.write_event(span)
    repository.write_event(final_trace)

    tree = repository.trace_tree(TRACE_ID)
    assert tree is not None
    assert tree.trace == final_trace
    assert tree.spans == (span,)


def test_final_trace_event_preserves_start_metadata_when_close_omits_it(tmp_path: Path) -> None:
    repository = Repository(Database.open(tmp_path / "glassbox.sqlite3"))
    trace, _, _, _ = _events()
    started = trace.model_copy(
        update={
            "input_ref": "sha256:input",
            "total_tokens": 123,
            "total_cost_usd": 0.42,
            "latency_ms": 17.5,
            "attributes": {"batch": 4},
        }
    )
    closing = TraceEvent(
        trace_id=trace.trace_id,
        agent_name=trace.agent_name,
        agent_version=trace.agent_version,
        started_at=trace.started_at,
        environment=trace.environment,
        ended_at=TIMESTAMP,
        status="error",
    )

    repository.write_event(started)
    repository.write_event(closing)

    tree = repository.trace_tree(TRACE_ID)
    assert tree is not None
    assert tree.trace.ended_at == TIMESTAMP
    assert tree.trace.status == "error"
    assert tree.trace.input_ref == "sha256:input"
    assert tree.trace.total_tokens == 123
    assert tree.trace.total_cost_usd == 0.42
    assert tree.trace.latency_ms == 17.5
    assert tree.trace.attributes == {"batch": 4}
