from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from glassbox.eval.judge_models import JudgeCandidate, JudgeCohort, JudgeOutcome, JudgeRun
from glassbox.events import DecisionEvent
from glassbox.store.repository import StoredDecision

DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAX"
TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
TIMESTAMP = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


def _decision() -> DecisionEvent:
    return DecisionEvent(
        decision_id=DECISION_ID,
        trace_id=TRACE_ID,
        agent_name="replenishment-triage-ai",
        agent_version="abc123",
        entity_type="sku_dc",
        entity_id="123-DC04",
        decision_type="flag_exception",
        recommendation={"action": "review"},
        rationale="Inventory risk is elevated.",
        rationale_citations=["inventory_position"],
        confidence=0.8,
        alternatives_considered=[],
        decided_at=TIMESTAMP,
    )


def test_judge_cohort_is_frozen_and_field_addressable() -> None:
    cohort = JudgeCohort(provider="openai", model="gpt-4o", rubric_version="reasoning_quality_v1")

    assert cohort.provider == "openai"
    assert cohort.model == "gpt-4o"
    assert cohort.rubric_version == "reasoning_quality_v1"
    with pytest.raises(dataclasses.FrozenInstanceError):
        cohort.provider = "anthropic"  # type: ignore[misc]


def test_judge_candidate_carries_decision_span_identities_and_flags() -> None:
    stored_decision = StoredDecision(event=_decision(), evidence=())

    candidate = JudgeCandidate(
        decision=stored_decision,
        llm_models=("gpt-4o",),
        llm_providers=("openai",),
        human_score=4,
        calibration_backlog=True,
        recent_gated=False,
    )

    assert candidate.decision.event.decision_id == DECISION_ID
    assert candidate.llm_models == ("gpt-4o",)
    assert candidate.llm_providers == ("openai",)
    assert candidate.human_score == 4
    assert candidate.calibration_backlog is True
    assert candidate.recent_gated is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        candidate.calibration_backlog = False  # type: ignore[misc]


def test_judge_outcome_distinguishes_success_and_failure_shapes() -> None:
    success = JudgeOutcome(
        decision_id=DECISION_ID,
        score=4,
        rationale="Well-grounded in the cited evidence.",
        error=None,
        self_judge_bypassed=False,
    )
    failure = JudgeOutcome(
        decision_id=DECISION_ID,
        score=None,
        rationale=None,
        error="judge response was not valid JSON",
        self_judge_bypassed=True,
    )

    assert success.score == 4
    assert success.error is None
    assert failure.score is None
    assert failure.error == "judge response was not valid JSON"
    with pytest.raises(dataclasses.FrozenInstanceError):
        success.score = 5  # type: ignore[misc]


def test_judge_run_carries_cohort_provenance_and_status() -> None:
    run = JudgeRun(
        eval_run_id="01ARZ3NDEKTSV4RRFFQ69G5FD0",
        cohort=JudgeCohort(
            provider="openai", model="gpt-4o", rubric_version="reasoning_quality_v1"
        ),
        run_at=TIMESTAMP,
        status="passed",
        status_reason="calibration gate satisfied",
        judge_failure_count=0,
        self_judge_allowed=False,
    )

    assert run.eval_run_id == "01ARZ3NDEKTSV4RRFFQ69G5FD0"
    assert run.cohort.provider == "openai"
    assert run.status == "passed"
    assert run.judge_failure_count == 0
    assert run.self_judge_allowed is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        run.status = "failed"  # type: ignore[misc]
