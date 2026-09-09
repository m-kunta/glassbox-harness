"""Immutable presentation models for the planner's read-only web views."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from glassbox.events import EvidenceEvent, SpanEvent, TraceEvent
from glassbox.events.models import canonical_dumps
from glassbox.store.repository import OverrideRecord, OverrideStatus, StoredDecision, TraceTree


class ConfidenceBand(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class Diagnostic:
    message: str


@dataclass(frozen=True)
class EvidenceGroupView:
    evidence_id: str
    anchor: str
    fields: tuple[EvidenceEvent, ...]


@dataclass(frozen=True)
class CitationView:
    evidence_id: str
    anchor: str | None
    diagnostic: str | None


@dataclass(frozen=True)
class OverrideView:
    status: OverrideStatus
    current: OverrideRecord | None
    records: tuple[OverrideRecord, ...]


@dataclass(frozen=True)
class DecisionCard:
    decision: StoredDecision
    recommendation_summary: str
    confidence_band: ConfidenceBand
    verdict: str
    evidence_groups: tuple[EvidenceGroupView, ...]
    citations: tuple[CitationView, ...]
    override: OverrideView
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True)
class SpanView:
    event: SpanEvent
    children: tuple[SpanView, ...]


@dataclass(frozen=True)
class TraceView:
    trace: TraceEvent
    roots: tuple[SpanView, ...]
    orphans: tuple[SpanView, ...]
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True)
class QueueRow:
    decision_id: str
    trace_id: str
    agent_name: str
    decision_type: str
    recommendation_summary: str
    confidence: float
    confidence_band: ConfidenceBand
    decided_at: str
    override_status: OverrideStatus
    sort_value: str | float


@dataclass(frozen=True)
class QueuePage:
    rows: tuple[QueueRow, ...]
    next_cursor: str | None


def confidence_band(confidence: float) -> ConfidenceBand:
    """Return the single display/filter band for a valid confidence value."""
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("confidence must be a finite value between 0 and 1")
    if confidence < 0.5:
        return ConfidenceBand.LOW
    if confidence < 0.8:
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.HIGH


def confidence_bounds(band: ConfidenceBand) -> tuple[float, float | None]:
    """Return the lower inclusive and upper exclusive filter bounds for *band*."""
    if band is ConfidenceBand.LOW:
        return (0.0, 0.5)
    if band is ConfidenceBand.MEDIUM:
        return (0.5, 0.8)
    return (0.8, None)


def recommendation_summary(recommendation: object, *, limit: int = 160) -> str:
    """Format opaque recommendation JSON deterministically for planner display."""
    if limit < 1:
        raise ValueError("summary limit must be positive")
    text = canonical_dumps(recommendation)
    return text if len(text) <= limit else f"{text[:limit - 1]}…"


def build_decision_card(
    decision: StoredDecision, overrides: tuple[OverrideRecord, ...]
) -> DecisionCard:
    """Convert a stored decision and override history into a stable card."""
    groups_by_id: dict[str, list[EvidenceEvent]] = {}
    for evidence in decision.evidence:
        groups_by_id.setdefault(evidence.evidence_id, []).append(evidence)
    groups = tuple(
        EvidenceGroupView(evidence_id, f"evidence-{index}", tuple(fields))
        for index, (evidence_id, fields) in enumerate(groups_by_id.items())
    )
    anchors = {group.evidence_id: group.anchor for group in groups}
    citations = tuple(
        CitationView(
            evidence_id,
            anchors.get(evidence_id),
            None if evidence_id in anchors else f"Unresolved evidence citation: {evidence_id}",
        )
        for evidence_id in decision.event.rationale_citations
    )
    diagnostics = tuple(
        Diagnostic(citation.diagnostic) for citation in citations if citation.diagnostic is not None
    )
    band = confidence_band(decision.event.confidence)
    summary = recommendation_summary(decision.event.recommendation)
    return DecisionCard(
        decision,
        summary,
        band,
        f"{summary} — {band.value.title()} ({decision.event.confidence:.2f})",
        groups,
        citations,
        _override_view(overrides),
        diagnostics,
    )


def build_trace_view(tree: TraceTree) -> TraceView:
    """Build a cycle-free, time-ordered span tree with visible diagnostics."""
    spans_by_id = {span.span_id: span for span in tree.spans}
    cycle_ids = _cycle_ids(spans_by_id)
    invalid_ids = set(cycle_ids)
    invalid_ids.update(
        span.span_id
        for span in tree.spans
        if span.parent_span_id is not None and span.parent_span_id not in spans_by_id
    )
    changed = True
    while changed:
        changed = False
        for span in tree.spans:
            if (
                span.span_id not in invalid_ids
                and span.parent_span_id is not None
                and span.parent_span_id in invalid_ids
            ):
                invalid_ids.add(span.span_id)
                changed = True

    children: dict[str, list[SpanEvent]] = {
        span_id: [] for span_id in spans_by_id if span_id not in invalid_ids
    }
    roots: list[SpanEvent] = []
    for span in tree.spans:
        if span.span_id in invalid_ids:
            continue
        if span.parent_span_id is None:
            roots.append(span)
        else:
            children[span.parent_span_id].append(span)

    def make_view(span: SpanEvent) -> SpanView:
        return SpanView(
            span,
            tuple(make_view(child) for child in sorted(children[span.span_id], key=_span_order)),
        )

    diagnostics: list[Diagnostic] = []
    if cycle_ids:
        diagnostics.append(Diagnostic("Span parent cycle detected."))
    if any(
        span.parent_span_id is not None and span.parent_span_id not in spans_by_id
        for span in tree.spans
    ):
        diagnostics.append(Diagnostic("Span parent is missing from this trace."))
    return TraceView(
        tree.trace,
        tuple(make_view(span) for span in sorted(roots, key=_span_order)),
        tuple(SpanView(spans_by_id[span_id], ()) for span_id in sorted(invalid_ids)),
        tuple(diagnostics),
    )


def _override_view(overrides: tuple[OverrideRecord, ...]) -> OverrideView:
    if not overrides:
        return OverrideView("none", None, overrides)
    superseded = {item.supersedes_override_id for item in overrides if item.supersedes_override_id}
    heads = tuple(item for item in overrides if item.override_id not in superseded)
    if len(heads) != 1:
        return OverrideView("inconsistent", None, overrides)
    return OverrideView(heads[0].action, heads[0], overrides)


def _cycle_ids(spans_by_id: dict[str, SpanEvent]) -> set[str]:
    cycle_ids: set[str] = set()
    for span_id in spans_by_id:
        path: list[str] = []
        seen_at: dict[str, int] = {}
        current_id: str | None = span_id
        while current_id is not None and current_id in spans_by_id:
            if current_id in seen_at:
                cycle_ids.update(path[seen_at[current_id] :])
                break
            seen_at[current_id] = len(path)
            path.append(current_id)
            current_id = spans_by_id[current_id].parent_span_id
    return cycle_ids


def _span_order(span: SpanEvent) -> tuple[object, str]:
    return (span.started_at, span.span_id)
