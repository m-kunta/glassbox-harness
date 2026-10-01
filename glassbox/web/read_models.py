"""Immutable presentation models for the planner's read-only web views."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from glassbox.eval.drift import DriftReport, DriftSignalReport
from glassbox.events import EvidenceEvent, SpanEvent, TraceEvent
from glassbox.events.models import canonical_dumps
from glassbox.store.repository import (
    FeedbackRecord,
    OverrideRecord,
    OverrideStatus,
    StoredDecision,
    TraceTree,
)


class ConfidenceBand(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class Diagnostic:
    message: str


@dataclass(frozen=True)
class AttributeView:
    """One readable key/value attribute from an opaque scalar mapping."""

    label: str
    value: str


@dataclass(frozen=True)
class ValueView:
    """One opaque value rendered as a scalar, attributes, or raw JSON."""

    scalar: str | None
    attributes: tuple[AttributeView, ...]
    raw_json: str | None


@dataclass(frozen=True)
class RecommendationView:
    """A recommendation's primary action plus neutral secondary attributes."""

    action: str
    attributes: tuple[AttributeView, ...]
    raw_json: str | None


@dataclass(frozen=True)
class FieldView:
    """One JSON-safe evidence field ready for template rendering."""

    field_name: str
    value: ValueView


@dataclass(frozen=True)
class EvidenceGroupView:
    evidence_id: str
    anchor: str
    fields: tuple[FieldView, ...]


@dataclass(frozen=True)
class AlternativeView:
    """A readable rejected alternative with a faithful JSON fallback."""

    summary: str
    detail: str | None


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
class FeedbackView:
    feedback_id: str
    verdict: str
    reason_code: str | None
    free_text: str | None
    corrected_recommendation: str | None
    created_at: str
    reasoning_quality_score: int | None
    reasoning_quality_rubric_version: str | None


@dataclass(frozen=True)
class DecisionCard:
    decision: StoredDecision
    recommendation_summary: str
    recommendation: RecommendationView
    recommendation_detail: str
    recommended_action: str
    entity_label: str
    confidence_band: ConfidenceBand
    verdict: str
    evidence_groups: tuple[EvidenceGroupView, ...]
    citations: tuple[CitationView, ...]
    alternatives: tuple[AlternativeView, ...]
    override: OverrideView
    feedback: tuple[FeedbackView, ...]
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
    entity_label: str
    recommendation: RecommendationView
    confidence: float
    confidence_band: ConfidenceBand
    decided_at: str
    override_status: OverrideStatus
    sort_value: int | float


@dataclass(frozen=True)
class QueuePage:
    rows: tuple[QueueRow, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class DriftLandingView:
    """The known cohorts a local operator may inspect."""

    agents: tuple[str, ...]


@dataclass(frozen=True)
class DriftSignalView:
    """One display-ready signal; no persistence or policy objects escape here."""

    name: str
    population_label: str
    algorithm: str
    status: str
    metric: str
    thresholds: str
    baseline_count: int
    recent_count: int
    detail_rows: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class DriftReportView:
    agent_name: str
    status: str
    action: str
    calculated_at: str
    policy_version: str
    policy_hash: str
    baseline_id: str | None
    baseline_range: str
    recent_range: str
    baseline_versions: tuple[AttributeView, ...]
    recent_versions: tuple[AttributeView, ...]
    decisions_per_trace: str
    signals: tuple[DriftSignalView, ...]


_DRIFT_ACTIONS = {
    "baseline_not_created": "No baseline exists for this agent. Run glassbox drift baseline.",
    "policy_changed_requires_rebaseline": (
        "A baseline exists for this agent under a different policy. "
        "Materialize a baseline for the changed policy."
    ),
    "baseline_window_too_small": (
        "The historical baseline window has too little usable data. "
        "Wait for more activity or choose a wider baseline window, then create a baseline."
    ),
    "recent_window_too_small": (
        "The recent window has too little usable data. Wait for more recent activity."
    ),
    None: "No operator action is required.",
}

_DRIFT_SIGNAL_NAMES = {
    "confidence": "Decision confidence",
    "decision_type": "Decision type",
    "trace_latency_ms": "Trace latency",
    "trace_cost_usd": "Trace cost",
}


def _drift_number(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "Not calculated"
    if not math.isfinite(float(value)):
        return "Not calculated"
    return f"{float(value):.4g}"


def _drift_versions(values: tuple[tuple[str, int, int], ...]) -> tuple[AttributeView, ...]:
    return tuple(
        AttributeView(version, f"{decisions} decisions · {traces} traces")
        for version, decisions, traces in values
    )


def _utc_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _drift_details(signal: DriftSignalReport) -> tuple[tuple[str, str, str], ...]:
    if signal.algorithm == "psi":
        labels = signal.details.get("bins", signal.details.get("categories", []))
        baseline = signal.details.get("baseline_counts", [])
        recent = signal.details.get("recent_counts", [])
        if (
            isinstance(labels, list)
            and isinstance(baseline, list)
            and isinstance(recent, list)
            and len(labels) == len(baseline) == len(recent)
        ):
            return tuple(
                (str(label), str(old), str(new))
                for label, old, new in zip(labels, baseline, recent)
            )
        return ()
    path = signal.details.get("cusum_path", [])
    if not isinstance(path, list):
        return ()
    rows: list[tuple[str, str, str]] = []
    for entry in path:
        if not isinstance(entry, dict):
            continue
        rows.append(
            (
                f"Sample {entry.get('sample', '?')}",
                _drift_number(entry.get("positive")),
                _drift_number(entry.get("negative")),
            )
        )
    return tuple(rows)


def build_drift_report_view(report: DriftReport) -> DriftReportView:
    """Render drift math as literal, non-agent-specific planner language."""
    signals = tuple(
        DriftSignalView(
            _DRIFT_SIGNAL_NAMES[signal.name],
            "Decision-level" if signal.population == "decision" else "Trace-level",
            signal.algorithm.upper(),
            signal.status.replace("_", " ").title(),
            _drift_number(signal.value),
            (
                f"Watch ≥ {_drift_number(signal.warning_threshold)} · "
                f"Alert ≥ {_drift_number(signal.alert_threshold)}"
            ),
            signal.baseline_count,
            signal.recent_count,
            _drift_details(signal),
        )
        for signal in report.signals
    )
    decisions_per_trace = "Not available"
    if (
        report.baseline_decisions_per_trace is not None
        or report.recent_decisions_per_trace is not None
    ):
        decisions_per_trace = (
            f"Baseline {_drift_number(report.baseline_decisions_per_trace)} · "
            f"Recent {_drift_number(report.recent_decisions_per_trace)}"
        )
    return DriftReportView(
        report.agent_name or "Unknown agent",
        report.status.replace("_", " ").title(),
        _DRIFT_ACTIONS[report.reason],
        report.as_of.isoformat().replace("+00:00", "Z"),
        report.policy_version,
        report.policy_hash,
        report.baseline_id,
        f"{_utc_text(report.baseline_start)} to {_utc_text(report.baseline_end)}",
        f"{_utc_text(report.recent_start)} to {_utc_text(report.recent_end)}",
        _drift_versions(report.baseline_versions),
        _drift_versions(report.recent_versions),
        decisions_per_trace,
        signals,
    )


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
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _json_display(value: object) -> str:
    """Format one opaque JSON value for safe, faithful planner display."""
    return canonical_dumps(value)


def _is_scalar(value: object) -> bool:
    return (
        value is None
        or isinstance(value, (bool, str, int))
        or (isinstance(value, float) and math.isfinite(value))
    )


def _scalar_text(value: object) -> str:
    if value is None:
        return "Not provided"
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return canonical_dumps(value)
    raise TypeError("value must be a displayable scalar")


def _label(key: str) -> str:
    return key.replace("_", " ").title()


def value_view(value: object) -> ValueView:
    """Return the sole safe presentation shape for an opaque JSON value."""
    if _is_scalar(value):
        return ValueView(_scalar_text(value), (), None)
    if isinstance(value, dict) and all(_is_scalar(item) for item in value.values()):
        return ValueView(
            None,
            tuple(AttributeView(_label(key), _scalar_text(item)) for key, item in value.items()),
            None,
        )
    return ValueView(None, (), _json_display(value))


def recommendation_view(value: object) -> RecommendationView:
    """Extract a conventional action without inferring semantics for other keys."""
    display = value_view(value)
    action = value.get("action") if isinstance(value, dict) else None
    return RecommendationView(
        action if isinstance(action, str) else "Not specified",
        tuple(attribute for attribute in display.attributes if attribute.label != "Action"),
        display.raw_json,
    )


def _alternative_view(value: object) -> AlternativeView:
    """Prefer a generic action/reason statement, retaining unknown shapes as JSON."""
    if isinstance(value, dict) and isinstance(action := value.get("action"), str):
        reason = value.get("reason")
        label = action.replace("_", " ").capitalize()
        if isinstance(reason, str) and reason:
            return AlternativeView(f"{label} — {reason}", None)
    return AlternativeView(_json_display(value), None)


def build_decision_card(
    decision: StoredDecision,
    overrides: tuple[OverrideRecord, ...],
    feedback: tuple[FeedbackRecord, ...] = (),
) -> DecisionCard:
    """Convert a stored decision and override history into a stable card."""
    event = decision.event.model_dump(mode="json")
    recommendation = event["recommendation"]
    groups_by_id: dict[str, list[EvidenceEvent]] = {}
    for evidence in decision.evidence:
        groups_by_id.setdefault(evidence.evidence_id, []).append(evidence)
    groups = tuple(
        EvidenceGroupView(
            evidence_id,
            f"evidence-{index}",
            tuple(
                FieldView(
                    field.field_name,
                    value_view(field.model_dump(mode="json")["field_value"]),
                )
                for field in fields
            ),
        )
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
    summary = recommendation_summary(recommendation)
    recommendation_display = recommendation_view(recommendation)
    return DecisionCard(
        decision,
        summary,
        recommendation_display,
        _json_display(recommendation),
        recommendation_display.action,
        f"{decision.event.entity_type} · {decision.event.entity_id}",
        band,
        f"{summary} — {band.value.title()} ({decision.event.confidence:.2f})",
        groups,
        citations,
        tuple(_alternative_view(value) for value in event["alternatives_considered"]),
        _override_view(overrides),
        tuple(
            FeedbackView(
                item.feedback_id,
                item.verdict,
                item.reason_code,
                item.free_text,
                None
                if item.corrected_recommendation is None
                else canonical_dumps(item.corrected_recommendation),
                item.created_at.isoformat().replace("+00:00", "Z"),
                item.reasoning_quality_score,
                item.reasoning_quality_rubric_version,
            )
            for item in feedback
        ),
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
