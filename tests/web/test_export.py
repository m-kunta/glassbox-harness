from __future__ import annotations

from datetime import UTC, datetime

import pytest

from glassbox.events import DecisionEvent
from glassbox.store.repository import StoredDecision
from glassbox.web.export import ExportError, render_decision_export, write_decision_export
from glassbox.web.read_models import build_decision_card


def _card():
    decision = DecisionEvent(
        decision_id="01ARZ3NDEKTSV4RRFFQ69G5FAX",
        trace_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        agent_name="planner",
        agent_version="v1",
        entity_type="sku",
        entity_id="sku-1",
        decision_type="replenish",
        recommendation={"action": "order"},
        rationale="<script>unsafe</script>",
        rationale_citations=(),
        confidence=0.8,
        alternatives_considered=("hold",),
        decided_at=datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
    )
    return build_decision_card(StoredDecision(decision, ()), ())


def test_export_is_standalone_read_only_and_escaped() -> None:
    html = render_decision_export(_card(), None)

    assert "<style>" in html
    assert "<form" not in html
    assert "csrf_token" not in html
    assert "<script" not in html
    assert 'href="/static/' not in html
    assert "&lt;script&gt;unsafe&lt;/script&gt;" in html


def test_export_writer_requires_explicit_overwrite(tmp_path) -> None:
    target = tmp_path / "card.html"
    target.write_text("old", encoding="utf-8")

    with pytest.raises(ExportError, match="--overwrite"):
        write_decision_export(target, "new", overwrite=False)

    write_decision_export(target, "new", overwrite=True)
    assert target.read_text(encoding="utf-8") == "new"
