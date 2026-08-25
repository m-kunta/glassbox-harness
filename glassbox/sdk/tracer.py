"""Non-invasive synchronous and asynchronous trace and span instrumentation."""

from __future__ import annotations

import inspect
import logging
import secrets
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from functools import wraps
from time import perf_counter
from typing import Any, Callable, Literal, TypeVar, cast

from glassbox.events import DecisionEvent, SpanEvent, TraceEvent

from .config import emit, get_config, redact
from .context import (
    DecisionState,
    TraceState,
    current_decision,
    current_span,
    current_trace,
    reset_decision,
    reset_span,
    reset_trace,
    set_decision,
    set_span,
    set_trace,
)

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_T = TypeVar("_T")


def trace(function: Callable[..., _T]) -> Callable[..., _T]:
    """Decorate a synchronous or async callable with a root trace when needed."""
    if inspect.iscoroutinefunction(function):

        @wraps(function)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            if current_trace.get() is not None or not get_config().enabled:
                return await function(*args, **kwargs)
            with _TraceScope():
                return await function(*args, **kwargs)

        return cast(Callable[..., _T], async_wrapper)

    @wraps(function)
    def sync_wrapper(*args: Any, **kwargs: Any) -> _T:
        if current_trace.get() is not None or not get_config().enabled:
            return function(*args, **kwargs)
        with _TraceScope():
            return function(*args, **kwargs)

    return sync_wrapper


def span(
    name: str, *, kind: Literal["llm", "retrieval", "tool", "compute"] = "compute"
) -> SpanScope:
    """Return a context manager which emits a completed child span."""
    return SpanScope(name=name, kind=kind)


def decision_context(*, entity_type: str, entity_id: str, decision_type: str) -> DecisionScope:
    """Return a context which exclusively owns evidence for one decision."""
    return DecisionScope(entity_type=entity_type, entity_id=entity_id, decision_type=decision_type)


class _TraceScope(AbstractContextManager[None]):
    def __init__(self) -> None:
        self._token: object | None = None
        self._state: TraceState | None = None

    def __enter__(self) -> None:
        config = get_config()
        started_at = _now()
        self._state = TraceState(
            trace_id=_new_ulid(started_at),
            agent=config.agent,
            version=config.version,
            environment=config.environment,
            started_at=started_at,
        )
        self._token = set_trace(self._state)
        _emit_trace(self._state)
        return None

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if self._state is not None:
            _emit_trace(self._state, ended_at=_now(), status="error" if exc_type else "ok")
        if self._token is not None:
            reset_trace(cast(Any, self._token))
        return None


class SpanScope(AbstractContextManager[None]):
    """Emit one completed span while preserving its parent span context."""

    def __init__(self, *, name: str, kind: Literal["llm", "retrieval", "tool", "compute"]) -> None:
        self._name = name
        self._kind = kind
        self._span_id: str | None = None
        self._parent_span_id: str | None = None
        self._started_at: datetime | None = None
        self._started_clock: float | None = None
        self._token: object | None = None
        self._trace: TraceState | None = None

    def __enter__(self) -> None:
        if not get_config().enabled or current_trace.get() is None:
            return None
        self._trace = current_trace.get()
        if self._trace is None:
            return None
        self._started_at = _now()
        self._started_clock = perf_counter()
        self._span_id = _new_ulid(self._started_at)
        self._parent_span_id = current_span.get()
        self._token = set_span(self._span_id)
        return None

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if self._token is not None:
            reset_span(cast(Any, self._token))
        if self._trace is not None and self._span_id is not None and self._started_at is not None:
            try:
                values: dict[str, Any] = {
                    "span_id": self._span_id,
                    "trace_id": self._trace.trace_id,
                    "name": redact(self._name),
                    "span_kind": self._kind,
                    "started_at": self._started_at,
                    "ended_at": _now(),
                    "latency_ms": _elapsed_ms(self._started_clock),
                }
                if self._parent_span_id is not None:
                    values["parent_span_id"] = self._parent_span_id
                emit(SpanEvent(**values))
            except Exception:
                pass
        return None


class DecisionScope(AbstractContextManager["DecisionScope"]):
    """Buffer evidence and emit it only with a completed owning decision."""

    def __init__(self, *, entity_type: str, entity_id: str, decision_type: str) -> None:
        self._entity_type = entity_type
        self._entity_id = entity_id
        self._decision_type = decision_type
        self._state: DecisionState | None = None
        self._token: object | None = None

    def __enter__(self) -> DecisionScope:
        trace_state = current_trace.get()
        if not get_config().enabled or trace_state is None:
            return self
        if current_decision.get() is not None:
            raise RuntimeError("nested decision contexts are not supported")
        self._state = DecisionState(
            decision_id=_new_ulid(_now()),
            trace=trace_state,
            entity_type=self._entity_type,
            entity_id=self._entity_id,
            decision_type=self._decision_type,
        )
        self._token = set_decision(self._state)
        return self

    def complete(
        self,
        *,
        recommendation: dict[str, Any],
        rationale: str,
        rationale_citations: list[str] | tuple[str, ...],
        confidence: float,
        alternatives_considered: list[Any] | tuple[Any, ...],
    ) -> None:
        """Commit this decision and its buffered evidence, without agent-visible failures."""
        if self._state is None or self._state.completed:
            return
        self._state.completed = True
        try:
            event = DecisionEvent(
                decision_id=self._state.decision_id,
                trace_id=self._state.trace.trace_id,
                agent_name=redact(self._state.trace.agent),
                agent_version=redact(self._state.trace.version),
                entity_type=redact(self._state.entity_type),
                entity_id=redact(self._state.entity_id),
                decision_type=redact(self._state.decision_type),
                recommendation=redact(recommendation),
                rationale=redact(rationale),
                rationale_citations=tuple(redact(citation) for citation in rationale_citations),
                confidence=confidence,
                alternatives_considered=redact(alternatives_considered),
                decided_at=_now(),
            )
            emit(event)
            for evidence_event in self._state.evidence:
                emit(evidence_event)
        except Exception:
            pass
        finally:
            self._state.evidence.clear()

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if self._state is not None:
            if not self._state.completed:
                self._state.evidence.clear()
                _warn_incomplete_decision()
            if self._token is not None:
                reset_decision(cast(Any, self._token))
        return None


_last_incomplete_warning: float | None = None


def _warn_incomplete_decision() -> None:
    global _last_incomplete_warning
    now = perf_counter()
    if _last_incomplete_warning is None or now - _last_incomplete_warning >= 60:
        _last_incomplete_warning = now
        logging.getLogger("glassbox.sdk").warning("discarded evidence for incomplete decision")


def _emit_trace(
    state: TraceState, *, ended_at: datetime | None = None, status: Literal["ok", "error"] = "ok"
) -> None:
    try:
        values: dict[str, Any] = {
            "trace_id": state.trace_id,
            "agent_name": redact(state.agent),
            "agent_version": redact(state.version),
            "started_at": state.started_at,
            "environment": redact(state.environment),
            "status": status,
        }
        if ended_at is not None:
            values["ended_at"] = ended_at
        emit(TraceEvent(**values))
    except Exception:
        pass


def _now() -> datetime:
    return datetime.now(UTC)


def _elapsed_ms(started: float | None) -> float | None:
    return None if started is None else (perf_counter() - started) * 1_000


def _new_ulid(timestamp: datetime) -> str:
    value = (int(timestamp.timestamp() * 1_000) << 80) | secrets.randbits(80)
    result = ""
    for _ in range(26):
        value, remainder = divmod(value, 32)
        result = _ALPHABET[remainder] + result
    return result


def reset_warning_for_testing() -> None:
    """Clear module-local warning state between isolated SDK tests."""
    global _last_incomplete_warning
    _last_incomplete_warning = None


__all__ = ["DecisionScope", "SpanScope", "decision_context", "span", "trace"]
