"""Dependency-neutral contracts shared by evaluation runners and adapters."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GoldenCase(BaseModel):
    """One stable, labeled input to an evaluation target."""

    model_config = ConfigDict(frozen=True)

    case_id: str = Field(min_length=1)
    input: dict[str, Any]
    expected_labels: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceRecord(BaseModel):
    """Evidence grouped by the caller-defined citation key."""

    model_config = ConfigDict(frozen=True)

    evidence_id: str = Field(min_length=1)
    fields: dict[str, Any]


class DecisionResult(BaseModel):
    """Structured target outcome consumed by deterministic checks."""

    model_config = ConfigDict(frozen=True)

    decision: dict[str, Any]
    evidence: tuple[EvidenceRecord, ...]
    rationale_citations: tuple[str, ...]
    measurements: dict[str, float | int] = Field(default_factory=dict)
    error: dict[str, str] | None = None
