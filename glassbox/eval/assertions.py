"""Deterministic checks over one structured evaluation result."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from jsonschema import ValidationError, validate

from .models import AssertionResult, DecisionResult


def evaluate_deterministic(
    result: DecisionResult, recommendation_schema: Mapping[str, Any]
) -> tuple[AssertionResult, ...]:
    """Evaluate P1's four uniform checks for one result."""
    try:
        validate(instance=result.decision, schema=dict(recommendation_schema))
        schema_result = AssertionResult(name="schema_valid", passed=True)
    except ValidationError as exc:
        schema_result = AssertionResult(name="schema_valid", passed=False, message=exc.message)

    evidence_ids = {evidence.evidence_id for evidence in result.evidence}
    missing_citations = sorted(set(result.rationale_citations) - evidence_ids)
    citation_result = AssertionResult(
        name="citations_resolve",
        passed=not missing_citations,
        message=(
            ""
            if not missing_citations
            else f"missing evidence IDs: {', '.join(missing_citations)}"
        ),
    )
    evidence_result = AssertionResult(
        name="evidence_present",
        passed=len(result.evidence) >= 3,
        message="" if len(result.evidence) >= 3 else "at least three evidence records required",
    )
    alternatives_result = AssertionResult(
        name="alternatives_present",
        passed=bool(result.alternatives_considered),
        message="" if result.alternatives_considered else "at least one alternative required",
    )
    return schema_result, citation_result, evidence_result, alternatives_result
