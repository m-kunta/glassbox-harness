"""Decision-scoped evidence capture."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from glassbox.events import EvidenceEvent

from .config import get_config, redact
from .context import current_decision


def evidence(
    *,
    evidence_id: str,
    source_system: str,
    source_ref: str,
    fields: Mapping[str, Any],
    weight: float = 1.0,
    retrieved_at: datetime | None = None,
) -> None:
    """Buffer fields as evidence owned exclusively by the active decision."""
    decision = current_decision.get()
    if not get_config().enabled or decision is None or decision.completed:
        return
    try:
        timestamp = retrieved_at or datetime.now(UTC)
        for field_name, field_value in fields.items():
            decision.evidence.append(
                EvidenceEvent(
                    evidence_id=redact(evidence_id),
                    decision_id=decision.decision_id,
                    source_system=redact(source_system),
                    source_ref=redact(source_ref),
                    field_name=redact(field_name),
                    field_value=redact(field_value),
                    weight=weight,
                    retrieved_at=timestamp,
                )
            )
    except Exception:
        # Dropping broken telemetry is preferable to changing agent behavior.
        return


__all__ = ["evidence"]
