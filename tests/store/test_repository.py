from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from glassbox.events import DecisionEvent, EvidenceEvent, SpanEvent, TraceEvent
from glassbox.store import Database, Repository
from glassbox.store.repository import (
    FeedbackSubmission,
    OverrideSubmission,
    QueueCursor,
    QueueQuery,
)

TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
SPAN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAX"
TIMESTAMP = datetime(2026, 8, 22, 14, 30, 45, 123000, tzinfo=UTC)
QUEUE_TRACE_IDS = (
    "01ARZ3NDEKTSV4RRFFQ69G5FB2",
    "01ARZ3NDEKTSV4RRFFQ69G5FB3",
    "01ARZ3NDEKTSV4RRFFQ69G5FB4",
    "01ARZ3NDEKTSV4RRFFQ69G5FB5",
)
QUEUE_DECISION_IDS = (
    "01ARZ3NDEKTSV4RRFFQ69G5FB6",
    "01ARZ3NDEKTSV4RRFFQ69G5FB7",
    "01ARZ3NDEKTSV4RRFFQ69G5FB8",
    "01ARZ3NDEKTSV4RRFFQ69G5FB9",
)
OVERRIDE_IDS = (
    "01ARZ3NDEKTSV4RRFFQ69G5FBA",
    "01ARZ3NDEKTSV4RRFFQ69G5FBB",
    "01ARZ3NDEKTSV4RRFFQ69G5FBC",
    "01ARZ3NDEKTSV4RRFFQ69G5FBD",
)
FEEDBACK_ID = "01ARZ3NDEKTSV4RRFFQ69G5FBF"


def _feedback_submission(
    *,
    feedback_id: str = FEEDBACK_ID,
    idempotency_key: str = "feedback-request-1",
    verdict: str = "agree",
) -> FeedbackSubmission:
    return FeedbackSubmission(
        feedback_id=feedback_id,
        decision_id=DECISION_ID,
        verdict=verdict,
        reason_code="inventory-confirmed",
        free_text="The recommendation matches the current inventory position.",
        corrected_recommendation={"action": "order"},
        created_at=TIMESTAMP,
        idempotency_key=idempotency_key,
    )


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


def _seed_queue_decision(
    repository: Repository,
    *,
    index: int,
    decided_at: datetime,
    confidence: float,
    agent_name: str = "replenishment-triage-ai",
    decision_type: str = "flag_exception",
) -> DecisionEvent:
    trace, _, decision, _ = _events()
    trace = trace.model_copy(
        update={
            "trace_id": QUEUE_TRACE_IDS[index],
            "agent_name": agent_name,
            "started_at": decided_at,
        }
    )
    decision = decision.model_copy(
        update={
            "decision_id": QUEUE_DECISION_IDS[index],
            "trace_id": trace.trace_id,
            "agent_name": agent_name,
            "decision_type": decision_type,
            "confidence": confidence,
            "decided_at": decided_at,
        }
    )
    repository.write_event(trace)
    repository.write_event(decision)
    return decision


def _insert_override(
    database: Database,
    *,
    override_id: str,
    decision_id: str,
    action: str,
    supersedes_override_id: str | None = None,
) -> None:
    database.connection.execute(
        """
        INSERT INTO overrides (
            override_id, decision_id, actor, action, created_at,
            supersedes_override_id, idempotency_key
        ) VALUES (?, ?, 'planner', ?, '2026-08-22T14:30:45.123Z', ?, ?)
        """,
        (override_id, decision_id, action, supersedes_override_id, f"request-{override_id}"),
    )
    database.connection.commit()


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


def test_feedback_is_append_only_idempotent_and_decision_scoped(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)
    trace, _, decision, _ = _events()
    repository.write_event(trace)
    repository.write_event(decision)
    submission = _feedback_submission()
    independent_replay = FeedbackSubmission(
        feedback_id="01ARZ3NDEKTSV4RRFFQ69G5FBK",
        decision_id=submission.decision_id,
        verdict=submission.verdict,
        reason_code=submission.reason_code,
        free_text=submission.free_text,
        corrected_recommendation=submission.corrected_recommendation,
        created_at=TIMESTAMP + timedelta(seconds=1),
        idempotency_key=submission.idempotency_key,
    )

    stored = repository.record_feedback(submission)
    replay = repository.record_feedback(independent_replay)
    second = repository.record_feedback(
        _feedback_submission(
            feedback_id="01ARZ3NDEKTSV4RRFFQ69G5FBG",
            idempotency_key="feedback-request-2",
        )
    )

    assert replay == stored
    assert repository.feedback_for_decision(DECISION_ID) == (second, stored)
    with pytest.raises(ValueError, match="idempotency"):
        repository.record_feedback(_feedback_submission(verdict="disagree"))
    with pytest.raises(sqlite3.IntegrityError):
        repository.record_feedback(
            FeedbackSubmission(
                feedback_id="01ARZ3NDEKTSV4RRFFQ69G5FBH",
                decision_id="01ARZ3NDEKTSV4RRFFQ69G5FBJ",
                verdict="agree",
                reason_code=None,
                free_text=None,
                corrected_recommendation=None,
                created_at=TIMESTAMP,
                idempotency_key="missing-decision",
            )
        )


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


def test_multiple_fields_under_one_evidence_id_all_persist(tmp_path: Path) -> None:
    """The SDK's evidence(fields={...}) call emits one row per field, all sharing
    one evidence_id (spec section 6's canonical example). They must not collide."""
    repository = Repository(Database.open(tmp_path / "glassbox.sqlite3"))
    trace, _, decision, evidence = _events()
    repository.write_event(trace)
    repository.write_event(decision)
    repository.write_event(evidence)
    second_field = evidence.model_copy(update={"field_name": "lead_time_var", "field_value": 3.2})

    repository.write_event(second_field)

    tree = repository.trace_tree(TRACE_ID)
    assert tree is not None
    fields = {item.field_name: item.field_value for item in tree.decisions[0].evidence}
    assert fields == {
        "on_hand": {"units": 0, "is_estimated": False, "notes": None},
        "lead_time_var": 3.2,
    }


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


def test_queue_uses_only_fixed_sort_columns(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)

    with pytest.raises(ValueError, match="sort column"):
        repository.queue(QueueQuery(sort_column="decided_at; DROP TABLE decisions"))  # type: ignore[arg-type]

    decision_table = database.connection.execute(
        "SELECT name FROM sqlite_master WHERE name = 'decisions'"
    ).fetchone()
    assert decision_table is not None


def test_queue_cursor_retains_every_tied_canonical_timestamp(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)
    _seed_queue_decision(repository, index=0, decided_at=TIMESTAMP, confidence=0.5)
    _seed_queue_decision(repository, index=1, decided_at=TIMESTAMP, confidence=0.8)

    first_page = repository.queue(QueueQuery(limit=1, sort_column="decided_at"))
    cursor = QueueCursor(first_page[0].sort_value, first_page[0].event.decision_id)
    second_page = repository.queue(QueueQuery(limit=10, sort_column="decided_at", cursor=cursor))

    seen = {row.event.decision_id for row in first_page + second_page}
    assert seen == {QUEUE_DECISION_IDS[0], QUEUE_DECISION_IDS[1]}


def test_queue_applies_bound_filters_and_confidence_ordering(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)
    _seed_queue_decision(repository, index=0, decided_at=TIMESTAMP, confidence=0.5)
    _seed_queue_decision(
        repository,
        index=1,
        decided_at=TIMESTAMP + timedelta(days=1),
        confidence=0.8,
        agent_name="other-agent",
    )
    _seed_queue_decision(
        repository,
        index=2,
        decided_at=TIMESTAMP + timedelta(days=2),
        confidence=0.9,
        decision_type="reorder",
    )

    rows = repository.queue(
        QueueQuery(
            agent_name="replenishment-triage-ai",
            decided_from=TIMESTAMP,
            decided_before=TIMESTAMP + timedelta(days=3),
            confidence_min=0.5,
            confidence_max=1.0,
            sort_column="confidence",
        )
    )

    assert [row.event.decision_id for row in rows] == [QUEUE_DECISION_IDS[2], QUEUE_DECISION_IDS[0]]
    assert repository.queue(QueueQuery(agent_name="'; DROP TABLE decisions; --")) == ()


def test_queue_and_detail_identify_override_heads_and_inconsistency(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)
    first = _seed_queue_decision(repository, index=0, decided_at=TIMESTAMP, confidence=0.5)
    second = _seed_queue_decision(repository, index=1, decided_at=TIMESTAMP, confidence=0.5)
    third = _seed_queue_decision(repository, index=2, decided_at=TIMESTAMP, confidence=0.5)

    _insert_override(
        database,
        override_id=OVERRIDE_IDS[0],
        decision_id=first.decision_id,
        action="accepted",
    )
    _insert_override(
        database,
        override_id=OVERRIDE_IDS[1],
        decision_id=first.decision_id,
        action="modified",
        supersedes_override_id=OVERRIDE_IDS[0],
    )
    _insert_override(
        database,
        override_id=OVERRIDE_IDS[2],
        decision_id=second.decision_id,
        action="accepted",
    )
    _insert_override(
        database,
        override_id=OVERRIDE_IDS[3],
        decision_id=second.decision_id,
        action="rejected",
    )
    _insert_override(
        database,
        override_id="01ARZ3NDEKTSV4RRFFQ69G5FBE",
        decision_id=third.decision_id,
        action="accepted",
        supersedes_override_id="01ARZ3NDEKTSV4RRFFQ69G5FBE",
    )

    rows = {row.event.decision_id: row for row in repository.queue(QueueQuery())}
    assert rows[first.decision_id].override_status == "modified"
    assert rows[second.decision_id].override_status == "inconsistent"
    assert rows[third.decision_id].override_status == "inconsistent"
    assert [
        row.event.decision_id for row in repository.queue(QueueQuery(override_status="modified"))
    ] == [first.decision_id]

    detail = repository.decision_detail(first.decision_id)
    assert detail is not None
    assert [override.override_id for override in detail.overrides] == [
        OVERRIDE_IDS[0],
        OVERRIDE_IDS[1],
    ]


def test_record_override_creates_a_linear_idempotent_history(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)
    trace, _, decision, _ = _events()
    repository.write_event(trace)
    repository.write_event(decision)
    first = OverrideSubmission(
        OVERRIDE_IDS[0],
        DECISION_ID,
        "planner",
        "accepted",
        None,
        None,
        None,
        TIMESTAMP,
        "override-request-1",
    )
    second = OverrideSubmission(
        OVERRIDE_IDS[1],
        DECISION_ID,
        "planner",
        "modified",
        {"action": "hold"},
        None,
        None,
        TIMESTAMP + timedelta(seconds=1),
        "override-request-2",
    )

    stored_first = repository.record_override(first)
    stored_second = repository.record_override(second)
    replay = repository.record_override(
        OverrideSubmission(
            OVERRIDE_IDS[2],
            DECISION_ID,
            "other",
            "modified",
            {"action": "hold"},
            None,
            None,
            TIMESTAMP + timedelta(seconds=2),
            "override-request-2",
        )
    )

    assert stored_first.supersedes_override_id is None
    assert stored_second.supersedes_override_id == OVERRIDE_IDS[0]
    assert replay == stored_second
