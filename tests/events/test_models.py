from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from glassbox.events import DecisionEvent, EvidenceEvent, SpanEvent, TraceEvent

TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
SPAN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAX"
EVIDENCE_ID = "inventory_position"
TIMESTAMP = datetime(2026, 8, 22, 14, 30, 45, 123000, tzinfo=UTC)


def test_trace_event_serializes_as_canonical_json() -> None:
    event = TraceEvent(
        trace_id=TRACE_ID,
        agent_name="replenishment-triage-ai",
        agent_version="abc123",
        started_at=TIMESTAMP,
        environment="shadow",
        attributes={"z": 1, "a": {"second": True, "first": None}},
    )

    assert event.canonical_json() == (
        '{"agent_name":"replenishment-triage-ai","agent_version":"abc123",'
        '"attributes":{"a":{"first":null,"second":true},"z":1},'
        '"environment":"shadow","started_at":"2026-08-22T14:30:45.123Z",'
        '"status":"ok",'
        f'"trace_id":"{TRACE_ID}"}}'
    )


def test_event_models_are_immutable() -> None:
    event = TraceEvent(
        trace_id=TRACE_ID,
        agent_name="replenishment-triage-ai",
        agent_version="abc123",
        started_at=TIMESTAMP,
        environment="shadow",
    )

    with pytest.raises(ValidationError):
        event.status = "error"  # type: ignore[misc]


def test_trace_attributes_are_recursively_immutable() -> None:
    event = TraceEvent(
        trace_id=TRACE_ID,
        agent_name="replenishment-triage-ai",
        agent_version="abc123",
        started_at=TIMESTAMP,
        environment="shadow",
        attributes={"inventory": {"positions": [0, 2]}},
    )

    with pytest.raises((TypeError, AttributeError)):
        event.attributes["inventory"]["positions"][0] = 5


def test_trace_attributes_do_not_expose_a_mutable_backing_store() -> None:
    event = TraceEvent(
        trace_id=TRACE_ID,
        agent_name="replenishment-triage-ai",
        agent_version="abc123",
        started_at=TIMESTAMP,
        environment="shadow",
        attributes={"inventory": 2},
    )

    with pytest.raises((TypeError, AttributeError)):
        event.attributes._values["inventory"] = 5  # type: ignore[attr-defined,index]

    assert event.attributes["inventory"] == 2


def test_decision_recommendation_is_recursively_immutable() -> None:
    event = DecisionEvent(
        decision_id=DECISION_ID,
        trace_id=TRACE_ID,
        agent_name="agent",
        agent_version="version",
        entity_type="sku_dc",
        entity_id="123-DC04",
        decision_type="flag_exception",
        recommendation={"actions": [{"type": "review"}]},
        rationale="Inventory risk is elevated.",
        rationale_citations=[EVIDENCE_ID],
        confidence=0.5,
        alternatives_considered=[],
        decided_at=TIMESTAMP,
    )

    with pytest.raises((TypeError, AttributeError)):
        event.recommendation["actions"][0]["type"] = "order"


def test_evidence_field_value_is_recursively_immutable() -> None:
    event = EvidenceEvent(
        evidence_id=EVIDENCE_ID,
        decision_id=DECISION_ID,
        source_system="BY_Fulfillment",
        source_ref="item_loc/123/DC04",
        field_name="on_hand",
        field_value={"history": [{"units": 0}]},
        weight=0.8,
        retrieved_at=TIMESTAMP,
    )

    with pytest.raises((TypeError, AttributeError)):
        event.field_value["history"][0]["units"] = 8


@pytest.mark.parametrize(
    ("model", "payload", "timestamp_field"),
    [
        (
            TraceEvent,
            {
                "trace_id": TRACE_ID,
                "agent_name": "agent",
                "agent_version": "version",
                "environment": "dev",
            },
            "started_at",
        ),
        (
            SpanEvent,
            {
                "span_id": SPAN_ID,
                "trace_id": TRACE_ID,
                "name": "retrieve_context",
                "span_kind": "retrieval",
            },
            "started_at",
        ),
        (
            DecisionEvent,
            {
                "decision_id": DECISION_ID,
                "trace_id": TRACE_ID,
                "agent_name": "agent",
                "agent_version": "version",
                "entity_type": "sku_dc",
                "entity_id": "123-DC04",
                "decision_type": "flag_exception",
                "recommendation": {"action": "review"},
                "rationale": "Inventory risk is elevated.",
                "rationale_citations": [EVIDENCE_ID],
                "confidence": 0.5,
                "alternatives_considered": [],
            },
            "decided_at",
        ),
        (
            EvidenceEvent,
            {
                "evidence_id": EVIDENCE_ID,
                "decision_id": DECISION_ID,
                "source_system": "BY_Fulfillment",
                "source_ref": "item_loc/123/DC04",
                "field_name": "on_hand",
                "field_value": {"units": 0},
                "weight": 0.8,
            },
            "retrieved_at",
        ),
    ],
)
def test_event_models_reject_naive_or_non_utc_timestamps(
    model: type[TraceEvent | SpanEvent | DecisionEvent | EvidenceEvent],
    payload: dict[str, object],
    timestamp_field: str,
) -> None:
    for timestamp in (
        datetime(2026, 8, 22, 14, 30),
        TIMESTAMP.astimezone(timezone(-timedelta(hours=4))),
    ):
        with pytest.raises(ValidationError, match="UTC"):
            model(**(payload | {timestamp_field: timestamp}))


@pytest.mark.parametrize("invalid_id", ["not-a-ulid", "01ARZ3NDEKTSV4RRFFQ69G5FAI"])
def test_trace_event_rejects_non_ulid_trace_ids(invalid_id: str) -> None:
    with pytest.raises(ValidationError, match="ULID"):
        TraceEvent(
            trace_id=invalid_id,
            agent_name="agent",
            agent_version="version",
            started_at=TIMESTAMP,
            environment="dev",
        )


def test_evidence_event_accepts_a_caller_defined_citation_key() -> None:
    event = EvidenceEvent(
        evidence_id="inventory_position",
        decision_id=DECISION_ID,
        source_system="BY_Fulfillment",
        source_ref="item_loc/123/DC04",
        field_name="on_hand",
        field_value=0,
        weight=0.8,
        retrieved_at=TIMESTAMP,
    )

    assert event.evidence_id == "inventory_position"


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_decision_event_rejects_confidence_outside_unit_interval(confidence: float) -> None:
    with pytest.raises(ValidationError):
        DecisionEvent(
            decision_id=DECISION_ID,
            trace_id=TRACE_ID,
            agent_name="agent",
            agent_version="version",
            entity_type="sku_dc",
            entity_id="123-DC04",
            decision_type="flag_exception",
            recommendation={"action": "review"},
            rationale="Inventory risk is elevated.",
            rationale_citations=[EVIDENCE_ID],
            confidence=confidence,
            alternatives_considered=[],
            decided_at=TIMESTAMP,
        )
