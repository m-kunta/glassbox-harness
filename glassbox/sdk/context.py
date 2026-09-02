"""Context-local trace, span, and decision state."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime

from glassbox.events import EvidenceEvent, SpanEvent


@dataclass
class TraceState:
    """Identity and timing data propagated through a trace."""

    trace_id: str
    agent: str
    version: str
    environment: str
    started_at: datetime
    input_ref: str | None = None


@dataclass
class SpanState:
    """The active span and its completed descendants awaiting persistence."""

    span_id: str
    completed_descendants: list[SpanEvent] = field(default_factory=list)


@dataclass
class DecisionState:
    """Evidence buffered until the owning decision is completed."""

    decision_id: str
    trace: TraceState
    entity_type: str
    entity_id: str
    decision_type: str
    evidence: list[EvidenceEvent] = field(default_factory=list)
    completed: bool = False


current_trace: ContextVar[TraceState | None] = ContextVar("glassbox_current_trace", default=None)
current_span: ContextVar[SpanState | None] = ContextVar("glassbox_current_span", default=None)
current_decision: ContextVar[DecisionState | None] = ContextVar(
    "glassbox_current_decision", default=None
)


def set_trace(state: TraceState) -> Token[TraceState | None]:
    """Set the active trace and return its restoration token."""
    return current_trace.set(state)


def reset_trace(token: Token[TraceState | None]) -> None:
    """Restore a previous trace state."""
    current_trace.reset(token)


def set_span(state: SpanState) -> Token[SpanState | None]:
    """Set the active span and return its restoration token."""
    return current_span.set(state)


def reset_span(token: Token[SpanState | None]) -> None:
    """Restore a previous span state."""
    current_span.reset(token)


def set_decision(state: DecisionState) -> Token[DecisionState | None]:
    """Set the active decision and return its restoration token."""
    return current_decision.set(state)


def reset_decision(token: Token[DecisionState | None]) -> None:
    """Restore a previous decision state."""
    current_decision.reset(token)
