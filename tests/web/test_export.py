from __future__ import annotations

from datetime import UTC, datetime

import pytest

from glassbox.events import DecisionEvent, EvidenceEvent
from glassbox.store.repository import StoredDecision
from glassbox.web.export import ExportError, render_decision_export, write_decision_export
from glassbox.web.read_models import build_decision_card


def _card(
    *,
    recommendation: object = {"action": "order", "threshold": 0.25},
    evidence_value: object = {"is_estimated": False, "units": 2},
):
    decision = DecisionEvent(
        decision_id="01ARZ3NDEKTSV4RRFFQ69G5FAX",
        trace_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        agent_name="planner",
        agent_version="v1",
        entity_type="sku",
        entity_id="sku-1",
        decision_type="replenish",
        recommendation=recommendation,
        rationale="<script>unsafe</script>",
        rationale_citations=(),
        confidence=0.8,
        alternatives_considered=("hold",),
        decided_at=datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
    )
    evidence = EvidenceEvent(
        evidence_id="inventory",
        decision_id=decision.decision_id,
        source_system="erp",
        source_ref="sku-1",
        field_name="available_units",
        field_value=evidence_value,
        weight=1.0,
        retrieved_at=decision.decided_at,
    )
    return build_decision_card(StoredDecision(decision, (evidence,)), ())


def test_export_is_standalone_read_only_and_escaped() -> None:
    html = render_decision_export(_card(), None)

    assert "<style>" in html
    assert "<form" not in html
    assert "csrf_token" not in html
    assert "<script" not in html
    assert 'href="/static/' not in html
    assert "&lt;script&gt;unsafe&lt;/script&gt;" in html
    assert "Threshold" in html
    assert "0.25" in html
    assert "Is Estimated" in html
    assert "No" in html
    assert '{&#34;action&#34;:&#34;order&#34;,&#34;threshold&#34;:0.25}' not in html
    assert "FrozenDict" not in html


def test_export_preserves_nested_values_as_canonical_json() -> None:
    html = render_decision_export(
        _card(
            recommendation={"action": "order", "constraints": {"minimum": 2}},
            evidence_value={"location": {"warehouse": "A"}},
        ),
        None,
    )

    assert '{&#34;action&#34;:&#34;order&#34;,&#34;constraints&#34;:{&#34;minimum&#34;:2}}' in html
    assert '{&#34;location&#34;:{&#34;warehouse&#34;:&#34;A&#34;}}' in html


def test_export_writer_requires_explicit_overwrite(tmp_path) -> None:
    target = tmp_path / "card.html"
    target.write_text("old", encoding="utf-8")

    with pytest.raises(ExportError, match="--overwrite"):
        write_decision_export(target, "new", overwrite=False)

    write_decision_export(target, "new", overwrite=True)
    assert target.read_text(encoding="utf-8") == "new"
