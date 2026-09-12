from __future__ import annotations

from datetime import UTC, datetime

import pytest

from glassbox.events import DecisionEvent, EvidenceEvent, SpanEvent, TraceEvent
from glassbox.store.repository import OverrideRecord, StoredDecision, TraceTree
from glassbox.web.read_models import (
    AttributeView,
    ConfidenceBand,
    build_decision_card,
    build_trace_view,
    confidence_band,
    confidence_bounds,
    recommendation_summary,
    value_view,
)

TIMESTAMP = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAX"


def _decision() -> DecisionEvent:
    return DecisionEvent(
        decision_id=DECISION_ID,
        trace_id=TRACE_ID,
        agent_name="agent",
        agent_version="v1",
        entity_type="sku",
        entity_id="sku-1",
        decision_type="replenish",
        recommendation={"action": "order"},
        rationale="Inventory is below safety stock.",
        rationale_citations=["inventory", "demand"],
        confidence=0.8,
        alternatives_considered=[{"action": "wait"}],
        decided_at=TIMESTAMP,
    )


def _stored_decision() -> StoredDecision:
    evidence = (
        EvidenceEvent(
            evidence_id="inventory",
            decision_id=DECISION_ID,
            source_system="erp",
            source_ref="sku-1",
            field_name="on_hand",
            field_value=0,
            weight=0.8,
            retrieved_at=TIMESTAMP,
        ),
        EvidenceEvent(
            evidence_id="inventory",
            decision_id=DECISION_ID,
            source_system="erp",
            source_ref="sku-1",
            field_name="on_order",
            field_value=10,
            weight=0.6,
            retrieved_at=TIMESTAMP,
        ),
    )
    return StoredDecision(_decision(), evidence)


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        (0.0, ConfidenceBand.LOW),
        (0.499999, ConfidenceBand.LOW),
        (0.5, ConfidenceBand.MEDIUM),
        (0.799999, ConfidenceBand.MEDIUM),
        (0.8, ConfidenceBand.HIGH),
        (1.0, ConfidenceBand.HIGH),
    ],
)
def test_confidence_band_boundaries(confidence: float, expected: ConfidenceBand) -> None:
    assert confidence_band(confidence) is expected


def test_confidence_bounds_match_the_display_bands() -> None:
    assert confidence_bounds(ConfidenceBand.LOW) == (0.0, 0.5)
    assert confidence_bounds(ConfidenceBand.MEDIUM) == (0.5, 0.8)
    assert confidence_bounds(ConfidenceBand.HIGH) == (0.8, None)


def test_recommendation_summary_is_canonical_and_truncated() -> None:
    summary = recommendation_summary({"z": "x" * 200, "a": 1}, limit=24)

    assert summary.startswith('{"a":1,"z":')
    assert summary.endswith("…")


def test_value_view_formats_scalar_mapping_without_inferred_units() -> None:
    view = value_view(
        {"is_estimated": False, "notes": None, "threshold": 0.25, "units": 0}
    )

    assert view.scalar is None
    assert view.raw_json is None
    assert view.attributes == (
        AttributeView("Is Estimated", "No"),
        AttributeView("Notes", "Not provided"),
        AttributeView("Threshold", "0.25"),
        AttributeView("Units", "0"),
    )


def test_value_view_keeps_nested_data_as_canonical_json() -> None:
    view = value_view({"units": 2, "source": {"warehouse": "A"}})

    assert view.attributes == ()
    assert view.raw_json == '{"source":{"warehouse":"A"},"units":2}'


def test_card_groups_evidence_and_marks_unresolved_citation() -> None:
    card = build_decision_card(_stored_decision(), ())

    assert [group.evidence_id for group in card.evidence_groups] == ["inventory"]
    assert len(card.evidence_groups[0].fields) == 2
    assert card.citations[0].anchor == "evidence-0"
    assert card.citations[1].diagnostic == "Unresolved evidence citation: demand"


def test_card_exposes_json_safe_decision_brief_values() -> None:
    stored = _stored_decision()
    evidence = stored.evidence[0].model_copy(update={"field_value": {"units": 0}})
    decision = stored.event.model_copy(
        update={
            "recommendation": {"action": "order", "threshold": 0.25},
            "alternatives_considered": [{"action": "wait", "reason": "incoming stock"}],
        }
    )

    card = build_decision_card(StoredDecision(decision, (evidence,)), ())

    assert card.entity_label == "sku · sku-1"
    assert card.recommended_action == "order"
    assert card.recommendation.attributes == (AttributeView("Threshold", "0.25"),)
    assert card.evidence_groups[0].fields[0].value.attributes == (AttributeView("Units", "0"),)
    assert card.alternatives[0].summary == "Wait — incoming stock"


def test_card_reports_inconsistent_override_history() -> None:
    override = OverrideRecord(
        override_id="01ARZ3NDEKTSV4RRFFQ69G5FAY",
        decision_id=DECISION_ID,
        actor="planner",
        action="accepted",
        modified_value=None,
        reason_code=None,
        free_text=None,
        created_at=TIMESTAMP,
        supersedes_override_id="01ARZ3NDEKTSV4RRFFQ69G5FAY",
    )

    card = build_decision_card(_stored_decision(), (override,))

    assert card.override.status == "inconsistent"


def test_trace_view_reports_missing_parent_and_cycle_without_cyclic_children() -> None:
    trace = TraceEvent(
        trace_id=TRACE_ID,
        agent_name="agent",
        agent_version="v1",
        started_at=TIMESTAMP,
        environment="dev",
    )
    orphan = SpanEvent(
        span_id="01ARZ3NDEKTSV4RRFFQ69G5FAW",
        trace_id=TRACE_ID,
        parent_span_id="01ARZ3NDEKTSV4RRFFQ69G5FB2",
        name="orphan",
        span_kind="tool",
        started_at=TIMESTAMP,
    )
    first_cycle = SpanEvent(
        span_id="01ARZ3NDEKTSV4RRFFQ69G5FB3",
        trace_id=TRACE_ID,
        parent_span_id="01ARZ3NDEKTSV4RRFFQ69G5FB4",
        name="first cycle",
        span_kind="tool",
        started_at=TIMESTAMP,
    )
    second_cycle = first_cycle.model_copy(
        update={"span_id": "01ARZ3NDEKTSV4RRFFQ69G5FB4", "parent_span_id": first_cycle.span_id}
    )

    view = build_trace_view(TraceTree(trace, (orphan, first_cycle, second_cycle), ()))

    assert not view.roots
    assert {span.event.span_id for span in view.orphans} == {
        orphan.span_id,
        first_cycle.span_id,
        second_cycle.span_id,
    }
    assert any("cycle" in diagnostic.message for diagnostic in view.diagnostics)
