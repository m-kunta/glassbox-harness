from __future__ import annotations

import dataclasses
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from glassbox.events import DecisionEvent, EvidenceEvent, SpanEvent, TraceEvent
from glassbox.store import Database, Repository
from glassbox.store.repository import (
    DriftBaselineConflictError,
    DriftBaselineInsert,
    DriftResultInsert,
    DriftRunInsert,
    FeedbackSubmission,
    JudgeCohort,
    JudgeOutcome,
    JudgeRun,
    OverrideSubmission,
    QueueCursor,
    QueueQuery,
)

DRIFT_START = datetime(2026, 9, 1, tzinfo=UTC)
DRIFT_MID = datetime(2026, 9, 8, tzinfo=UTC)
DRIFT_END = datetime(2026, 9, 15, tzinfo=UTC)
DRIFT_HASH = "a" * 64
DRIFT_SIGNALS = ("confidence", "decision_type", "trace_latency_ms", "trace_cost_usd")


@pytest.fixture
def drift_repository(tmp_path: Path) -> Repository:
    return Repository(Database.open(tmp_path / "drift.sqlite3"))


def _drift_baseline(baseline_id: str) -> DriftBaselineInsert:
    return DriftBaselineInsert(
        baseline_id=baseline_id,
        agent_name="agent-a",
        policy_version="v1",
        policy_hash=DRIFT_HASH,
        baseline_start=DRIFT_START,
        baseline_end=DRIFT_MID,
        created_at=DRIFT_END,
        version_counts={"v1": {"decisions": 3, "traces": 1}},
        reference={name: {"count": 1} for name in DRIFT_SIGNALS},
    )


def _drift_run(baseline_id: str) -> DriftRunInsert:
    return DriftRunInsert(
        drift_run_id="01ARZ3NDEKTSV4RRFFQ69G5FE2",
        baseline_id=baseline_id,
        agent_name="agent-a",
        policy_version="v1",
        policy_hash=DRIFT_HASH,
        as_of=DRIFT_END,
        recent_start=DRIFT_MID,
        recent_end=DRIFT_END,
        status="healthy",
        status_reason=None,
        version_counts={},
        context={},
    )


def _drift_results() -> tuple[DriftResultInsert, ...]:
    return tuple(
        DriftResultInsert(
            drift_result_id=f"01ARZ3NDEKTSV4RRFFQ69G5FE{index + 3}",
            signal_name=name,
            population="decision" if index < 2 else "trace",
            algorithm="psi" if index < 2 else "cusum",
            status="healthy",
            baseline_count=1,
            recent_count=1,
            metric_value=None if index == 3 else 0.0,
            warning_threshold=0.1,
            alert_threshold=0.2,
            details={},
        )
        for index, name in enumerate(DRIFT_SIGNALS)
    )


def test_drift_source_populations_and_agent_selection(drift_repository: Repository) -> None:
    connection = drift_repository._connection
    for index, (name, start, version) in enumerate(
        (
            ("agent-a", "2026-09-01T00:00:00Z", "v1"),
            ("agent-a", "2026-09-08T00:00:00Z", "v2"),
            ("agent-b", "2026-09-02T00:00:00Z", "v1"),
        )
    ):
        trace_id = f"01ARZ3NDEKTSV4RRFFQ69G5FF{index}"
        connection.execute(
            "INSERT INTO traces (trace_id, agent_name, agent_version, started_at, "
            "status, environment, latency_ms) VALUES (?, ?, ?, ?, 'ok', 'dev', NULL)",
            (trace_id, name, version, start),
        )
        for number in range(3 if index == 0 else 1):
            connection.execute(
                "INSERT INTO decisions (decision_id, trace_id, agent_name, agent_version, "
                "entity_type, entity_id, decision_type, recommendation, rationale, "
                "rationale_citations, confidence, alternatives_considered, decided_at) "
                "VALUES (?, ?, ?, ?, 'sku', 'one', 'flag', '{}', 'r', '[]', 0.5, '[]', ?)",
                (f"01ARZ3NDEKTSV4RRFFQ69G5FG{index * 3 + number}", trace_id, name, version, start),
            )
    connection.commit()
    source = drift_repository.drift_source_data(
        "agent-a", DRIFT_START, DRIFT_MID, DRIFT_MID, DRIFT_END
    )
    assert drift_repository.drift_agent_names() == ("agent-a", "agent-b")
    assert len(source.baseline_decisions) == 3
    assert len(source.baseline_traces) == 1
    assert source.baseline_traces[0].decision_count == 3
    assert source.baseline_traces[0].latency_ms is None
    assert source.baseline_traces[0].total_cost_usd is None
    assert len(source.recent_decisions) == len(source.recent_traces) == 1
    assert source.recent_traces[0].agent_version == "v2"


def test_drift_recent_source_uses_only_recent_bounds(drift_repository: Repository) -> None:
    connection = drift_repository._connection
    for index, stamp in enumerate(("2026-09-01T00:00:00Z", "2026-09-08T00:00:00Z")):
        connection.execute(
            "INSERT INTO traces (trace_id, agent_name, agent_version, started_at, "
            "status, environment) VALUES (?, 'agent-a', 'v1', ?, 'ok', 'dev')",
            (f"01ARZ3NDEKTSV4RRFFQ69G5FJ{index}", stamp),
        )
        connection.execute(
            "INSERT INTO decisions (decision_id, trace_id, agent_name, agent_version, "
            "entity_type, entity_id, decision_type, recommendation, rationale, "
            "rationale_citations, confidence, alternatives_considered, decided_at) "
            "VALUES (?, ?, 'agent-a', 'v1', 'sku', 'one', 'flag', '{}', 'r', '[]', 0.5, '[]', ?)",
            (f"01ARZ3NDEKTSV4RRFFQ69G5FK{index}", f"01ARZ3NDEKTSV4RRFFQ69G5FJ{index}", stamp),
        )
    connection.commit()
    queries: list[str] = []
    connection.set_trace_callback(queries.append)
    try:
        source = drift_repository.drift_recent_source_data("agent-a", DRIFT_MID, DRIFT_END)
    finally:
        connection.set_trace_callback(None)
    assert source.baseline_decisions == source.baseline_traces == ()
    assert len(source.recent_decisions) == len(source.recent_traces) == 1
    assert len(queries) == 2
    assert all("2026-09-01" not in query for query in queries)


def test_drift_baseline_heads_are_immutable_and_classified(drift_repository: Repository) -> None:
    first = drift_repository.record_drift_baseline(_drift_baseline("01ARZ3NDEKTSV4RRFFQ69G5FE0"))
    assert drift_repository.drift_baseline_state("agent-a", DRIFT_HASH).status == "active"
    with pytest.raises(DriftBaselineConflictError):
        drift_repository.record_drift_baseline(_drift_baseline("01ARZ3NDEKTSV4RRFFQ69G5FE1"))
    second = drift_repository.record_drift_baseline(
        _drift_baseline("01ARZ3NDEKTSV4RRFFQ69G5FE1"), supersede=True
    )
    assert second.supersedes_baseline_id == first.baseline_id
    assert drift_repository.active_drift_baseline("agent-a", DRIFT_HASH) == second
    assert drift_repository.drift_baseline_state("agent-b", DRIFT_HASH).status == "absent"
    assert (
        drift_repository._connection.execute("SELECT count(*) FROM drift_baselines").fetchone()[0]
        == 2
    )


def test_drift_run_rolls_back_invalid_result(drift_repository: Repository) -> None:
    baseline = drift_repository.record_drift_baseline(_drift_baseline("01ARZ3NDEKTSV4RRFFQ69G5FE0"))
    results = _drift_results()
    run = drift_repository.record_drift_run(_drift_run(baseline.baseline_id), results)
    assert run.baseline_id == baseline.baseline_id
    assert (
        drift_repository._connection.execute("SELECT count(*) FROM drift_results").fetchone()[0]
        == 4
    )
    bad_run = dataclasses.replace(
        _drift_run(baseline.baseline_id), drift_run_id="01ARZ3NDEKTSV4RRFFQ69G5FE9"
    )
    bad_result = dataclasses.replace(
        results[0], drift_result_id="01ARZ3NDEKTSV4RRFFQ69G5FEA", signal_name="bad"
    )
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        drift_repository.record_drift_run(bad_run, (bad_result, *results[1:]))
    assert (
        drift_repository._connection.execute("SELECT count(*) FROM drift_runs").fetchone()[0] == 1
    )


def test_drift_baseline_sql_failure_rolls_back(drift_repository: Repository) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        drift_repository.record_drift_baseline(_drift_baseline("invalid"))
    assert not drift_repository._connection.in_transaction
    assert drift_repository.drift_baseline_state("agent-a", DRIFT_HASH).status == "absent"


def test_drift_baseline_rejects_non_json_reference_before_sql(
    drift_repository: Repository,
) -> None:
    request = dataclasses.replace(
        _drift_baseline("01ARZ3NDEKTSV4RRFFQ69G5FE0"),
        reference={
            **_drift_baseline("01ARZ3NDEKTSV4RRFFQ69G5FE0").reference,
            "confidence": {"mean": float("nan")},
        },
    )
    with pytest.raises(ValueError, match="non-finite"):
        drift_repository.record_drift_baseline(request)
    assert not drift_repository._connection.in_transaction


def test_drift_baseline_rejects_multiple_and_zero_heads(drift_repository: Repository) -> None:
    first_id = "01ARZ3NDEKTSV4RRFFQ69G5FE0"
    second_id = "01ARZ3NDEKTSV4RRFFQ69G5FE1"
    drift_repository.record_drift_baseline(_drift_baseline(first_id))
    connection = drift_repository._connection
    connection.execute(
        """INSERT INTO drift_baselines
        SELECT ?, agent_name, policy_version, policy_hash, baseline_start, baseline_end,
               created_at, version_counts_json, reference_json, NULL
        FROM drift_baselines WHERE baseline_id = ?""",
        (second_id, first_id),
    )
    connection.commit()
    assert drift_repository.drift_baseline_state("agent-a", DRIFT_HASH).status == "malformed"
    with pytest.raises(DriftBaselineConflictError):
        drift_repository.record_drift_baseline(
            _drift_baseline("01ARZ3NDEKTSV4RRFFQ69G5FEB"), supersede=True
        )
    connection.execute("DELETE FROM drift_baselines WHERE baseline_id = ?", (second_id,))
    connection.execute(
        "UPDATE drift_baselines SET supersedes_baseline_id = baseline_id WHERE baseline_id = ?",
        (first_id,),
    )
    connection.commit()
    assert drift_repository.drift_baseline_state("agent-a", DRIFT_HASH).status == "malformed"
    with pytest.raises(DriftBaselineConflictError):
        drift_repository.active_drift_baseline("agent-a", DRIFT_HASH)


def test_drift_run_sql_failure_rolls_back_and_foreign_keys_restrict(
    drift_repository: Repository,
) -> None:
    baseline = drift_repository.record_drift_baseline(_drift_baseline("01ARZ3NDEKTSV4RRFFQ69G5FE0"))
    results = _drift_results()
    bad_results = (dataclasses.replace(results[0], drift_result_id="bad"), *results[1:])
    with pytest.raises(sqlite3.IntegrityError):
        drift_repository.record_drift_run(_drift_run(baseline.baseline_id), bad_results)
    assert (
        drift_repository._connection.execute("SELECT count(*) FROM drift_runs").fetchone()[0] == 0
    )
    drift_repository.record_drift_run(_drift_run(baseline.baseline_id), results)
    with pytest.raises(sqlite3.IntegrityError):
        drift_repository._connection.execute(
            "DELETE FROM drift_baselines WHERE baseline_id = ?", (baseline.baseline_id,)
        )
    drift_repository._connection.rollback()


def test_drift_source_bounds_use_timestamp_keys(drift_repository: Repository) -> None:
    connection = drift_repository._connection
    for index, stamp in enumerate(
        (
            "2026-09-01T00:00:45Z",
            "2026-09-01T00:00:45.123Z",
            "2026-09-01T00:00:45.123456Z",
        )
    ):
        connection.execute(
            "INSERT INTO traces (trace_id, agent_name, agent_version, started_at, "
            "status, environment) VALUES (?, 'agent-a', 'v1', ?, 'ok', 'dev')",
            (f"01ARZ3NDEKTSV4RRFFQ69G5FH{index}", stamp),
        )
    connection.commit()
    lower = datetime(2026, 9, 1, 0, 0, 45, 100_000, tzinfo=UTC)
    upper = datetime(2026, 9, 1, 0, 0, 45, 123_456, tzinfo=UTC)
    source = drift_repository.drift_source_data("agent-a", lower, upper, upper, DRIFT_MID)
    assert len(source.baseline_traces) == 1
    assert source.baseline_traces[0].started_at.microsecond == 123_000
    assert len(source.recent_traces) == 1
    assert source.recent_traces[0].started_at.microsecond == 123_456


TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
SPAN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAX"
TIMESTAMP = datetime(2026, 8, 22, 14, 30, 45, 123000, tzinfo=UTC)
QUEUE_TRACE_IDS = (
    "01ARZ3NDEKTSV4RRFFQ69G5FB2",
    "01ARZ3NDEKTSV4RRFFQ69G5FB3",
    "01ARZ3NDEKTSV4RRFFQ69G5FB4",
    "01ARZ3NDEKTSV4RRFFQ69G5FB5",
)
QUEUE_DECISION_IDS = (
    "01ARZ3NDEKTSV4RRFFQ69G5FB6",
    "01ARZ3NDEKTSV4RRFFQ69G5FB7",
    "01ARZ3NDEKTSV4RRFFQ69G5FB8",
    "01ARZ3NDEKTSV4RRFFQ69G5FB9",
)
OVERRIDE_IDS = (
    "01ARZ3NDEKTSV4RRFFQ69G5FBA",
    "01ARZ3NDEKTSV4RRFFQ69G5FBB",
    "01ARZ3NDEKTSV4RRFFQ69G5FBC",
    "01ARZ3NDEKTSV4RRFFQ69G5FBD",
)
FEEDBACK_ID = "01ARZ3NDEKTSV4RRFFQ69G5FBF"

TIMESTAMP_ORDER_FEEDBACK_TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FC0"
TIMESTAMP_ORDER_FEEDBACK_DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FC1"
TIMESTAMP_ORDER_FEEDBACK_IDS = (
    "01ARZ3NDEKTSV4RRFFQ69G5FC2",
    "01ARZ3NDEKTSV4RRFFQ69G5FC3",
    "01ARZ3NDEKTSV4RRFFQ69G5FC4",
)
TIMESTAMP_ORDER_OVERRIDE_IDS = (
    "01ARZ3NDEKTSV4RRFFQ69G5FC5",
    "01ARZ3NDEKTSV4RRFFQ69G5FC6",
    "01ARZ3NDEKTSV4RRFFQ69G5FC7",
)
TIMESTAMP_ORDER_TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FC8"
TIMESTAMP_ORDER_DECISION_IDS = (
    "01ARZ3NDEKTSV4RRFFQ69G5FC9",
    "01ARZ3NDEKTSV4RRFFQ69G5FCA",
    "01ARZ3NDEKTSV4RRFFQ69G5FCB",
)

# The three canonical UTC precisions the schema allows for one and the same
# instant's second: whole seconds, milliseconds, and microseconds. Comparing
# the raw RFC3339 text sorts "...:45Z" after "...:45.123...Z" (`Z` > `.` in
# ASCII), even though the plain-seconds instant is chronologically earliest.
TIMESTAMP_ORDER_SECONDS = datetime(2026, 9, 27, 12, 0, 45, 0, tzinfo=UTC)
TIMESTAMP_ORDER_MILLIS = datetime(2026, 9, 27, 12, 0, 45, 123_000, tzinfo=UTC)
TIMESTAMP_ORDER_MICROS = datetime(2026, 9, 27, 12, 0, 45, 123_456, tzinfo=UTC)

JUDGE_COHORT = JudgeCohort(provider="openai", model="gpt-4o", rubric_version="reasoning_quality_v1")
JUDGE_SINCE = datetime(2026, 6, 1, tzinfo=UTC)

JUDGE_BACKLOG_AND_RECENT_TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FD0"
JUDGE_BACKLOG_AND_RECENT_DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FD1"
JUDGE_BACKLOG_AND_RECENT_SPAN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FD2"
JUDGE_BACKLOG_ONLY_TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FD3"
JUDGE_BACKLOG_ONLY_DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FD4"
JUDGE_RECENT_ONLY_TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FD5"
JUDGE_RECENT_ONLY_DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FD6"
JUDGE_BOUNDARY_TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FD7"
JUDGE_BOUNDARY_DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FD8"

JUDGE_FAIL_ONLY_TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FDA"
JUDGE_FAIL_ONLY_DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FDB"
JUDGE_SUCCESS_THEN_FAIL_TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FDC"
JUDGE_SUCCESS_THEN_FAIL_DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FDD"
JUDGE_FAIL_THEN_SUCCESS_TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FDE"
JUDGE_FAIL_THEN_SUCCESS_DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FDF"

JUDGE_EVAL_RUN_ID_FAIL_ONLY = "01ARZ3NDEKTSV4RRFFQ69G5FDG"
JUDGE_EVAL_RUN_ID_SUCCESS_THEN_FAIL_OLD = "01ARZ3NDEKTSV4RRFFQ69G5FDH"
JUDGE_EVAL_RUN_ID_SUCCESS_THEN_FAIL_NEW = "01ARZ3NDEKTSV4RRFFQ69G5FDJ"
JUDGE_EVAL_RUN_ID_FAIL_THEN_SUCCESS_OLD = "01ARZ3NDEKTSV4RRFFQ69G5FDK"
JUDGE_EVAL_RUN_ID_FAIL_THEN_SUCCESS_NEW = "01ARZ3NDEKTSV4RRFFQ69G5FDM"
JUDGE_EVAL_RESULT_ID_FAIL_ONLY = "01ARZ3NDEKTSV4RRFFQ69G5FDN"
JUDGE_EVAL_RESULT_ID_SUCCESS_THEN_FAIL_OLD = "01ARZ3NDEKTSV4RRFFQ69G5FDP"
JUDGE_EVAL_RESULT_ID_SUCCESS_THEN_FAIL_NEW = "01ARZ3NDEKTSV4RRFFQ69G5FDQ"
JUDGE_EVAL_RESULT_ID_FAIL_THEN_SUCCESS_OLD = "01ARZ3NDEKTSV4RRFFQ69G5FDR"
JUDGE_EVAL_RESULT_ID_FAIL_THEN_SUCCESS_NEW = "01ARZ3NDEKTSV4RRFFQ69G5FDS"

JUDGE_RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FDT"
JUDGE_OK_TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FDV"
JUDGE_OK_DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FDW"
JUDGE_FAILED_TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FDX"
JUDGE_FAILED_DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FDY"
JUDGE_MISSING_DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FZZ"


def _judge_timestamp_text(value: datetime) -> str:
    assert value.microsecond == 0
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_decision_for_judge(
    repository: Repository, *, trace_id: str, decision_id: str, decided_at: datetime
) -> DecisionEvent:
    trace, _, decision, _ = _events()
    trace = trace.model_copy(update={"trace_id": trace_id, "started_at": decided_at})
    decision = decision.model_copy(
        update={"decision_id": decision_id, "trace_id": trace_id, "decided_at": decided_at}
    )
    repository.write_event(trace)
    repository.write_event(decision)
    return decision


def _seed_llm_span(
    repository: Repository,
    *,
    span_id: str,
    trace_id: str,
    started_at: datetime,
    model: str,
    provider: str | None = None,
) -> None:
    repository.write_event(
        SpanEvent(
            span_id=span_id,
            trace_id=trace_id,
            name="generate_recommendation",
            span_kind="llm",
            started_at=started_at,
            model=model,
            attributes={} if provider is None else {"gen_ai.provider.name": provider},
        )
    )


def _seed_scored_feedback(
    repository: Repository,
    *,
    feedback_id: str,
    decision_id: str,
    created_at: datetime,
    score: int,
    idempotency_key: str,
    rubric_version: str = "reasoning_quality_v1",
) -> None:
    repository.record_feedback(
        FeedbackSubmission(
            feedback_id=feedback_id,
            decision_id=decision_id,
            verdict="agree",
            reason_code=None,
            free_text=None,
            corrected_recommendation=None,
            created_at=created_at,
            idempotency_key=idempotency_key,
            reasoning_quality_score=score,
            reasoning_quality_rubric_version=rubric_version,
        )
    )


def _insert_judge_eval_run(
    database: Database,
    *,
    eval_run_id: str,
    run_at: datetime,
    cohort: JudgeCohort = JUDGE_COHORT,
) -> None:
    database.connection.execute(
        """
        INSERT INTO eval_runs (
            eval_run_id, suite_id, suite_version, agent_version, run_at, run_kind,
            judge_provider, judge_model, rubric_version, judge_temperature,
            self_judge_allowed, status, status_reason, judge_failure_count
        ) VALUES (
            ?, 'judge-calibration', 'v1', 'judge', ?, 'judge', ?, ?, ?, 0, 0, 'passed', 'ok', 0
        )
        """,
        (
            eval_run_id,
            _judge_timestamp_text(run_at),
            cohort.provider,
            cohort.model,
            cohort.rubric_version,
        ),
    )
    database.connection.commit()


def _insert_judge_eval_result(
    database: Database,
    *,
    eval_result_id: str,
    eval_run_id: str,
    decision_id: str,
    run_at: datetime,
    score: float | None,
) -> None:
    database.connection.execute(
        """
        INSERT INTO eval_results (
            eval_result_id, eval_run_id, case_id, assertion_name, passed, score,
            judge_rationale, run_at, decision_id, self_judge_bypassed
        ) VALUES (?, ?, ?, 'reasoning_quality', ?, ?, ?, ?, ?, 0)
        """,
        (
            eval_result_id,
            eval_run_id,
            decision_id,
            1 if score is not None else 0,
            score,
            "ok" if score is not None else "judge error",
            _judge_timestamp_text(run_at),
            decision_id,
        ),
    )
    database.connection.commit()


def _feedback_submission(
    *,
    feedback_id: str = FEEDBACK_ID,
    idempotency_key: str = "feedback-request-1",
    verdict: str = "agree",
    reasoning_quality_score: int | None = None,
    reasoning_quality_rubric_version: str | None = None,
) -> FeedbackSubmission:
    return FeedbackSubmission(
        feedback_id=feedback_id,
        decision_id=DECISION_ID,
        verdict=verdict,
        reason_code="inventory-confirmed",
        free_text="The recommendation matches the current inventory position.",
        corrected_recommendation={"action": "order"},
        created_at=TIMESTAMP,
        idempotency_key=idempotency_key,
        reasoning_quality_score=reasoning_quality_score,
        reasoning_quality_rubric_version=reasoning_quality_rubric_version,
    )


def _events() -> tuple[TraceEvent, SpanEvent, DecisionEvent, EvidenceEvent]:
    trace = TraceEvent(
        trace_id=TRACE_ID,
        agent_name="replenishment-triage-ai",
        agent_version="abc123",
        started_at=TIMESTAMP,
        environment="shadow",
        attributes={"batch": 4, "dry_run": False, "unset": None},
    )
    span = SpanEvent(
        span_id=SPAN_ID,
        trace_id=TRACE_ID,
        name="retrieve_context",
        span_kind="retrieval",
        started_at=TIMESTAMP,
        attributes={"filters": ["inventory", 7]},
    )
    decision = DecisionEvent(
        decision_id=DECISION_ID,
        trace_id=TRACE_ID,
        agent_name="replenishment-triage-ai",
        agent_version="abc123",
        entity_type="sku_dc",
        entity_id="123-DC04",
        decision_type="flag_exception",
        recommendation={"action": "review", "threshold": 0.25},
        rationale="Inventory risk is elevated.",
        rationale_citations=["inventory_position"],
        confidence=0.8,
        alternatives_considered=[{"action": "ignore", "reason": "not enough risk"}],
        decided_at=TIMESTAMP,
    )
    evidence = EvidenceEvent(
        evidence_id="inventory_position",
        decision_id=DECISION_ID,
        source_system="BY_Fulfillment",
        source_ref="item_loc/123/DC04",
        field_name="on_hand",
        field_value={"units": 0, "is_estimated": False, "notes": None},
        weight=0.8,
        retrieved_at=TIMESTAMP,
    )
    return trace, span, decision, evidence


def _seed_queue_decision(
    repository: Repository,
    *,
    index: int,
    decided_at: datetime,
    confidence: float,
    agent_name: str = "replenishment-triage-ai",
    decision_type: str = "flag_exception",
) -> DecisionEvent:
    trace, _, decision, _ = _events()
    trace = trace.model_copy(
        update={
            "trace_id": QUEUE_TRACE_IDS[index],
            "agent_name": agent_name,
            "started_at": decided_at,
        }
    )
    decision = decision.model_copy(
        update={
            "decision_id": QUEUE_DECISION_IDS[index],
            "trace_id": trace.trace_id,
            "agent_name": agent_name,
            "decision_type": decision_type,
            "confidence": confidence,
            "decided_at": decided_at,
        }
    )
    repository.write_event(trace)
    repository.write_event(decision)
    return decision


def _insert_override(
    database: Database,
    *,
    override_id: str,
    decision_id: str,
    action: str,
    supersedes_override_id: str | None = None,
) -> None:
    database.connection.execute(
        """
        INSERT INTO overrides (
            override_id, decision_id, actor, action, created_at,
            supersedes_override_id, idempotency_key
        ) VALUES (?, ?, 'planner', ?, '2026-08-22T14:30:45.123Z', ?, ?)
        """,
        (override_id, decision_id, action, supersedes_override_id, f"request-{override_id}"),
    )
    database.connection.commit()


def test_write_event_round_trips_typed_json_in_trace_tree(tmp_path: Path) -> None:
    repository = Repository(Database.open(tmp_path / "glassbox.sqlite3"))
    trace, span, decision, evidence = _events()

    for event in (trace, span, decision, evidence):
        repository.write_event(event)

    tree = repository.trace_tree(TRACE_ID)

    assert tree is not None
    assert tree.trace == trace
    assert tree.spans == (span,)
    assert tree.decisions[0].event == decision
    assert tree.decisions[0].evidence == (evidence,)
    assert tree.trace.attributes == {"batch": 4, "dry_run": False, "unset": None}
    assert tree.decisions[0].event.recommendation == {"action": "review", "threshold": 0.25}
    assert tree.decisions[0].evidence[0].field_value == {
        "units": 0,
        "is_estimated": False,
        "notes": None,
    }


def test_write_event_is_atomic_when_database_rejects_a_foreign_key(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)
    _, span, _, _ = _events()

    with pytest.raises(sqlite3.IntegrityError):
        repository.write_event(span)

    assert database.connection.execute("SELECT COUNT(*) FROM spans").fetchone()[0] == 0


def test_feedback_is_append_only_idempotent_and_decision_scoped(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)
    trace, _, decision, _ = _events()
    repository.write_event(trace)
    repository.write_event(decision)
    submission = _feedback_submission()
    independent_replay = FeedbackSubmission(
        feedback_id="01ARZ3NDEKTSV4RRFFQ69G5FBK",
        decision_id=submission.decision_id,
        verdict=submission.verdict,
        reason_code=submission.reason_code,
        free_text=submission.free_text,
        corrected_recommendation=submission.corrected_recommendation,
        created_at=TIMESTAMP + timedelta(seconds=1),
        idempotency_key=submission.idempotency_key,
    )

    stored = repository.record_feedback(submission)
    replay = repository.record_feedback(independent_replay)
    second = repository.record_feedback(
        _feedback_submission(
            feedback_id="01ARZ3NDEKTSV4RRFFQ69G5FBG",
            idempotency_key="feedback-request-2",
        )
    )

    assert replay == stored
    assert repository.feedback_for_decision(DECISION_ID) == (second, stored)
    with pytest.raises(ValueError, match="idempotency"):
        repository.record_feedback(_feedback_submission(verdict="disagree"))
    with pytest.raises(sqlite3.IntegrityError):
        repository.record_feedback(
            FeedbackSubmission(
                feedback_id="01ARZ3NDEKTSV4RRFFQ69G5FBH",
                decision_id="01ARZ3NDEKTSV4RRFFQ69G5FBJ",
                verdict="agree",
                reason_code=None,
                free_text=None,
                corrected_recommendation=None,
                created_at=TIMESTAMP,
                idempotency_key="missing-decision",
            )
        )


def test_feedback_reasoning_quality_score_and_rubric_version_round_trip(tmp_path: Path) -> None:
    """FeedbackSubmission/FeedbackRecord's reasoning-quality fields must reach
    the database and come back out through the public Repository API, not
    just satisfy the raw schema CHECK -- this is the P3 feature's whole
    point: a planner-facing score, not merely a column that exists."""
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)
    trace, _, decision, _ = _events()
    repository.write_event(trace)
    repository.write_event(decision)

    scored = repository.record_feedback(
        _feedback_submission(
            feedback_id="01ARZ3NDEKTSV4RRFFQ69G5FCC",
            idempotency_key="feedback-request-scored",
            reasoning_quality_score=4,
            reasoning_quality_rubric_version="reasoning_quality_v1",
        )
    )
    unscored = repository.record_feedback(
        _feedback_submission(
            feedback_id="01ARZ3NDEKTSV4RRFFQ69G5FCD",
            idempotency_key="feedback-request-unscored",
        )
    )

    assert scored.reasoning_quality_score == 4
    assert scored.reasoning_quality_rubric_version == "reasoning_quality_v1"
    assert unscored.reasoning_quality_score is None
    assert unscored.reasoning_quality_rubric_version is None

    fetched = {
        record.feedback_id: record for record in repository.feedback_for_decision(DECISION_ID)
    }
    assert fetched[scored.feedback_id] == scored
    assert fetched[unscored.feedback_id] == unscored

    # An exact replay (same score and rubric version) returns the stored record.
    replay = repository.record_feedback(
        _feedback_submission(
            feedback_id="01ARZ3NDEKTSV4RRFFQ69G5FCE",
            idempotency_key="feedback-request-scored",
            reasoning_quality_score=4,
            reasoning_quality_rubric_version="reasoning_quality_v1",
        )
    )
    assert replay == scored

    # A replay under the same idempotency key with a different score is rejected.
    with pytest.raises(ValueError, match="idempotency"):
        repository.record_feedback(
            _feedback_submission(
                feedback_id="01ARZ3NDEKTSV4RRFFQ69G5FCF",
                idempotency_key="feedback-request-scored",
                reasoning_quality_score=5,
                reasoning_quality_rubric_version="reasoning_quality_v1",
            )
        )


@pytest.mark.parametrize(
    ("score", "rubric_version"),
    [
        (0, "reasoning_quality_v1"),
        (6, "reasoning_quality_v1"),
        (3, None),
        (None, "reasoning_quality_v1"),
        (3, ""),
    ],
)
def test_feedback_reasoning_quality_score_is_validated_before_the_database(
    tmp_path: Path, score: int | None, rubric_version: str | None
) -> None:
    """A repository-level ValueError is a better failure mode than a raw
    sqlite3.IntegrityError bubbling up from the CHECK constraint."""
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)
    trace, _, decision, _ = _events()
    repository.write_event(trace)
    repository.write_event(decision)

    with pytest.raises(ValueError, match="reasoning quality"):
        repository.record_feedback(
            _feedback_submission(
                feedback_id="01ARZ3NDEKTSV4RRFFQ69G5FCG",
                idempotency_key="feedback-request-invalid-score",
                reasoning_quality_score=score,
                reasoning_quality_rubric_version=rubric_version,
            )
        )


def test_duplicate_caller_evidence_key_is_rejected_without_replacing_prior_evidence(
    tmp_path: Path,
) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)
    trace, _, decision, evidence = _events()
    repository.write_event(trace)
    repository.write_event(decision)
    repository.write_event(evidence)

    with pytest.raises(sqlite3.IntegrityError):
        repository.write_event(evidence)

    tree = repository.trace_tree(TRACE_ID)
    assert tree is not None
    assert tree.decisions[0].evidence == (evidence,)


def test_multiple_fields_under_one_evidence_id_all_persist(tmp_path: Path) -> None:
    """The SDK's evidence(fields={...}) call emits one row per field, all sharing
    one evidence_id (spec section 6's canonical example). They must not collide."""
    repository = Repository(Database.open(tmp_path / "glassbox.sqlite3"))
    trace, _, decision, evidence = _events()
    repository.write_event(trace)
    repository.write_event(decision)
    repository.write_event(evidence)
    second_field = evidence.model_copy(update={"field_name": "lead_time_var", "field_value": 3.2})

    repository.write_event(second_field)

    tree = repository.trace_tree(TRACE_ID)
    assert tree is not None
    fields = {item.field_name: item.field_value for item in tree.decisions[0].evidence}
    assert fields == {
        "on_hand": {"units": 0, "is_estimated": False, "notes": None},
        "lead_time_var": 3.2,
    }


def test_mark_trace_partial_updates_an_existing_trace(tmp_path: Path) -> None:
    repository = Repository(Database.open(tmp_path / "glassbox.sqlite3"))
    trace, _, _, _ = _events()
    repository.write_event(trace)

    repository.mark_trace_partial(TRACE_ID)

    tree = repository.trace_tree(TRACE_ID)
    assert tree is not None
    assert tree.trace.status == "partial"


def test_mark_trace_partial_leaves_missing_trace_uncreated(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)

    repository.mark_trace_partial(TRACE_ID)

    assert repository.trace_tree(TRACE_ID) is None
    assert database.connection.execute("SELECT COUNT(*) FROM traces").fetchone()[0] == 0


def test_final_trace_event_updates_the_open_trace_without_losing_children(tmp_path: Path) -> None:
    repository = Repository(Database.open(tmp_path / "glassbox.sqlite3"))
    trace, span, _, _ = _events()
    final_trace = trace.model_copy(update={"ended_at": TIMESTAMP, "status": "error"})

    repository.write_event(trace)
    repository.write_event(span)
    repository.write_event(final_trace)

    tree = repository.trace_tree(TRACE_ID)
    assert tree is not None
    assert tree.trace == final_trace
    assert tree.spans == (span,)


def test_final_trace_event_preserves_start_metadata_when_close_omits_it(tmp_path: Path) -> None:
    repository = Repository(Database.open(tmp_path / "glassbox.sqlite3"))
    trace, _, _, _ = _events()
    started = trace.model_copy(
        update={
            "input_ref": "sha256:input",
            "total_tokens": 123,
            "total_cost_usd": 0.42,
            "latency_ms": 17.5,
            "attributes": {"batch": 4},
        }
    )
    closing = TraceEvent(
        trace_id=trace.trace_id,
        agent_name=trace.agent_name,
        agent_version=trace.agent_version,
        started_at=trace.started_at,
        environment=trace.environment,
        ended_at=TIMESTAMP,
        status="error",
    )

    repository.write_event(started)
    repository.write_event(closing)

    tree = repository.trace_tree(TRACE_ID)
    assert tree is not None
    assert tree.trace.ended_at == TIMESTAMP
    assert tree.trace.status == "error"
    assert tree.trace.input_ref == "sha256:input"
    assert tree.trace.total_tokens == 123
    assert tree.trace.total_cost_usd == 0.42
    assert tree.trace.latency_ms == 17.5
    assert tree.trace.attributes == {"batch": 4}


def test_queue_uses_only_fixed_sort_columns(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)

    with pytest.raises(ValueError, match="sort column"):
        repository.queue(QueueQuery(sort_column="decided_at; DROP TABLE decisions"))  # type: ignore[arg-type]

    decision_table = database.connection.execute(
        "SELECT name FROM sqlite_master WHERE name = 'decisions'"
    ).fetchone()
    assert decision_table is not None


def test_queue_cursor_retains_every_tied_canonical_timestamp(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)
    _seed_queue_decision(repository, index=0, decided_at=TIMESTAMP, confidence=0.5)
    _seed_queue_decision(repository, index=1, decided_at=TIMESTAMP, confidence=0.8)

    first_page = repository.queue(QueueQuery(limit=1, sort_column="decided_at"))
    cursor = QueueCursor(first_page[0].sort_value, first_page[0].event.decision_id)
    second_page = repository.queue(QueueQuery(limit=10, sort_column="decided_at", cursor=cursor))

    seen = {row.event.decision_id for row in first_page + second_page}
    assert seen == {QUEUE_DECISION_IDS[0], QUEUE_DECISION_IDS[1]}


def test_queue_applies_bound_filters_and_confidence_ordering(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)
    _seed_queue_decision(repository, index=0, decided_at=TIMESTAMP, confidence=0.5)
    _seed_queue_decision(
        repository,
        index=1,
        decided_at=TIMESTAMP + timedelta(days=1),
        confidence=0.8,
        agent_name="other-agent",
    )
    _seed_queue_decision(
        repository,
        index=2,
        decided_at=TIMESTAMP + timedelta(days=2),
        confidence=0.9,
        decision_type="reorder",
    )

    rows = repository.queue(
        QueueQuery(
            agent_name="replenishment-triage-ai",
            decided_from=TIMESTAMP,
            decided_before=TIMESTAMP + timedelta(days=3),
            confidence_min=0.5,
            confidence_max=1.0,
            sort_column="confidence",
        )
    )

    assert [row.event.decision_id for row in rows] == [QUEUE_DECISION_IDS[2], QUEUE_DECISION_IDS[0]]
    assert repository.queue(QueueQuery(agent_name="'; DROP TABLE decisions; --")) == ()


def test_queue_and_detail_identify_override_heads_and_inconsistency(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)
    first = _seed_queue_decision(repository, index=0, decided_at=TIMESTAMP, confidence=0.5)
    second = _seed_queue_decision(repository, index=1, decided_at=TIMESTAMP, confidence=0.5)
    third = _seed_queue_decision(repository, index=2, decided_at=TIMESTAMP, confidence=0.5)

    _insert_override(
        database,
        override_id=OVERRIDE_IDS[0],
        decision_id=first.decision_id,
        action="accepted",
    )
    _insert_override(
        database,
        override_id=OVERRIDE_IDS[1],
        decision_id=first.decision_id,
        action="modified",
        supersedes_override_id=OVERRIDE_IDS[0],
    )
    _insert_override(
        database,
        override_id=OVERRIDE_IDS[2],
        decision_id=second.decision_id,
        action="accepted",
    )
    _insert_override(
        database,
        override_id=OVERRIDE_IDS[3],
        decision_id=second.decision_id,
        action="rejected",
    )
    _insert_override(
        database,
        override_id="01ARZ3NDEKTSV4RRFFQ69G5FBE",
        decision_id=third.decision_id,
        action="accepted",
        supersedes_override_id="01ARZ3NDEKTSV4RRFFQ69G5FBE",
    )

    rows = {row.event.decision_id: row for row in repository.queue(QueueQuery())}
    assert rows[first.decision_id].override_status == "modified"
    assert rows[second.decision_id].override_status == "inconsistent"
    assert rows[third.decision_id].override_status == "inconsistent"
    assert [
        row.event.decision_id for row in repository.queue(QueueQuery(override_status="modified"))
    ] == [first.decision_id]

    detail = repository.decision_detail(first.decision_id)
    assert detail is not None
    assert [override.override_id for override in detail.overrides] == [
        OVERRIDE_IDS[0],
        OVERRIDE_IDS[1],
    ]


def test_record_override_creates_a_linear_idempotent_history(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)
    trace, _, decision, _ = _events()
    repository.write_event(trace)
    repository.write_event(decision)
    first = OverrideSubmission(
        OVERRIDE_IDS[0],
        DECISION_ID,
        "planner",
        "accepted",
        None,
        None,
        None,
        TIMESTAMP,
        "override-request-1",
    )
    second = OverrideSubmission(
        OVERRIDE_IDS[1],
        DECISION_ID,
        "planner",
        "modified",
        {"action": "hold"},
        None,
        None,
        TIMESTAMP + timedelta(seconds=1),
        "override-request-2",
    )

    stored_first = repository.record_override(first)
    stored_second = repository.record_override(second)
    replay = repository.record_override(
        OverrideSubmission(
            OVERRIDE_IDS[2],
            DECISION_ID,
            "other",
            "modified",
            {"action": "hold"},
            None,
            None,
            TIMESTAMP + timedelta(seconds=2),
            "override-request-2",
        )
    )

    assert stored_first.supersedes_override_id is None
    assert stored_second.supersedes_override_id == OVERRIDE_IDS[0]
    assert replay == stored_second


def test_timestamp_order_uses_instants_not_rfc3339_text(tmp_path: Path) -> None:
    repository = Repository(Database.open(tmp_path / "glassbox.sqlite3"))
    trace, _, decision, _ = _events()

    feedback_trace = trace.model_copy(
        update={
            "trace_id": TIMESTAMP_ORDER_FEEDBACK_TRACE_ID,
            "started_at": TIMESTAMP_ORDER_SECONDS,
        }
    )
    feedback_decision = decision.model_copy(
        update={
            "decision_id": TIMESTAMP_ORDER_FEEDBACK_DECISION_ID,
            "trace_id": feedback_trace.trace_id,
            "decided_at": TIMESTAMP_ORDER_SECONDS,
        }
    )
    repository.write_event(feedback_trace)
    repository.write_event(feedback_decision)

    for feedback_id, created_at in zip(
        TIMESTAMP_ORDER_FEEDBACK_IDS,
        (TIMESTAMP_ORDER_MILLIS, TIMESTAMP_ORDER_SECONDS, TIMESTAMP_ORDER_MICROS),
        strict=True,
    ):
        repository.record_feedback(
            FeedbackSubmission(
                feedback_id=feedback_id,
                decision_id=feedback_decision.decision_id,
                verdict="agree",
                reason_code=None,
                free_text=None,
                corrected_recommendation=None,
                created_at=created_at,
                idempotency_key=f"timestamp-order-feedback-{feedback_id}",
            )
        )

    feedback_records = repository.feedback_for_decision(feedback_decision.decision_id)
    assert [record.feedback_id for record in feedback_records] == [
        TIMESTAMP_ORDER_FEEDBACK_IDS[2],  # microseconds: newest first
        TIMESTAMP_ORDER_FEEDBACK_IDS[0],  # milliseconds
        TIMESTAMP_ORDER_FEEDBACK_IDS[1],  # seconds: oldest last
    ]

    for override_id, created_at in zip(
        TIMESTAMP_ORDER_OVERRIDE_IDS,
        (TIMESTAMP_ORDER_MICROS, TIMESTAMP_ORDER_SECONDS, TIMESTAMP_ORDER_MILLIS),
        strict=True,
    ):
        repository.record_override(
            OverrideSubmission(
                override_id,
                feedback_decision.decision_id,
                "planner",
                "accepted",
                None,
                None,
                None,
                created_at,
                f"timestamp-order-override-{override_id}",
            )
        )

    detail = repository.decision_detail(feedback_decision.decision_id)
    assert detail is not None
    assert [override.override_id for override in detail.overrides] == [
        TIMESTAMP_ORDER_OVERRIDE_IDS[1],  # seconds: oldest first
        TIMESTAMP_ORDER_OVERRIDE_IDS[2],  # milliseconds
        TIMESTAMP_ORDER_OVERRIDE_IDS[0],  # microseconds: newest last
    ]

    decision_trace = trace.model_copy(
        update={"trace_id": TIMESTAMP_ORDER_TRACE_ID, "started_at": TIMESTAMP_ORDER_SECONDS}
    )
    repository.write_event(decision_trace)
    for decision_id, decided_at in zip(
        TIMESTAMP_ORDER_DECISION_IDS,
        (TIMESTAMP_ORDER_MICROS, TIMESTAMP_ORDER_SECONDS, TIMESTAMP_ORDER_MILLIS),
        strict=True,
    ):
        repository.write_event(
            decision.model_copy(
                update={
                    "decision_id": decision_id,
                    "trace_id": decision_trace.trace_id,
                    "decided_at": decided_at,
                }
            )
        )

    tree = repository.trace_tree(decision_trace.trace_id)
    assert tree is not None
    assert [item.event.decision_id for item in tree.decisions] == [
        TIMESTAMP_ORDER_DECISION_IDS[1],  # seconds: earliest first
        TIMESTAMP_ORDER_DECISION_IDS[2],  # milliseconds
        TIMESTAMP_ORDER_DECISION_IDS[0],  # microseconds: latest last
    ]


# --- Judge calibration: candidate selection and run persistence (P3a Task 3) ---


def test_judge_candidates_returns_deduplicated_backlog_then_recent_groups_in_order(
    tmp_path: Path,
) -> None:
    """Candidate union order is calibration backlog first (newest decided_at
    first), then recent-only decisions (newest decided_at first). A decision
    in both groups appears once, in the backlog slot, with both flags true."""
    repository = Repository(Database.open(tmp_path / "glassbox.sqlite3"))

    backlog_and_recent = _seed_decision_for_judge(
        repository,
        trace_id=JUDGE_BACKLOG_AND_RECENT_TRACE_ID,
        decision_id=JUDGE_BACKLOG_AND_RECENT_DECISION_ID,
        decided_at=datetime(2026, 6, 10, tzinfo=UTC),
    )
    _seed_scored_feedback(
        repository,
        feedback_id="01ARZ3NDEKTSV4RRFFQ69G5FE0",
        decision_id=backlog_and_recent.decision_id,
        created_at=datetime(2026, 6, 10, 1, tzinfo=UTC),
        score=5,
        idempotency_key="judge-feedback-backlog-and-recent",
    )
    _seed_llm_span(
        repository,
        span_id=JUDGE_BACKLOG_AND_RECENT_SPAN_ID,
        trace_id=JUDGE_BACKLOG_AND_RECENT_TRACE_ID,
        started_at=datetime(2026, 6, 10, tzinfo=UTC),
        model="gpt-4o",
        provider="openai",
    )

    backlog_only = _seed_decision_for_judge(
        repository,
        trace_id=JUDGE_BACKLOG_ONLY_TRACE_ID,
        decision_id=JUDGE_BACKLOG_ONLY_DECISION_ID,
        decided_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    _seed_scored_feedback(
        repository,
        feedback_id="01ARZ3NDEKTSV4RRFFQ69G5FE1",
        decision_id=backlog_only.decision_id,
        created_at=datetime(2026, 5, 1, 1, tzinfo=UTC),
        score=3,
        idempotency_key="judge-feedback-backlog-only",
    )

    recent_only = _seed_decision_for_judge(
        repository,
        trace_id=JUDGE_RECENT_ONLY_TRACE_ID,
        decision_id=JUDGE_RECENT_ONLY_DECISION_ID,
        decided_at=datetime(2026, 6, 5, tzinfo=UTC),
    )

    candidates = repository.judge_candidates(JUDGE_COHORT, JUDGE_SINCE)

    assert [
        (c.decision.event.decision_id, c.calibration_backlog, c.recent_gated) for c in candidates
    ] == [
        (backlog_and_recent.decision_id, True, True),
        (backlog_only.decision_id, True, False),
        (recent_only.decision_id, False, True),
    ]

    by_id = {c.decision.event.decision_id: c for c in candidates}
    assert by_id[backlog_and_recent.decision_id].human_score == 5
    assert by_id[backlog_and_recent.decision_id].llm_models == ("gpt-4o",)
    assert by_id[backlog_and_recent.decision_id].llm_providers == ("openai",)
    assert by_id[backlog_only.decision_id].human_score == 3
    assert by_id[backlog_only.decision_id].llm_models == ()
    assert by_id[backlog_only.decision_id].llm_providers == ()
    assert by_id[recent_only.decision_id].human_score is None


def test_judge_candidates_decided_since_boundary_is_inclusive(tmp_path: Path) -> None:
    repository = Repository(Database.open(tmp_path / "glassbox.sqlite3"))
    boundary = _seed_decision_for_judge(
        repository,
        trace_id=JUDGE_BOUNDARY_TRACE_ID,
        decision_id=JUDGE_BOUNDARY_DECISION_ID,
        decided_at=JUDGE_SINCE,
    )

    candidates = repository.judge_candidates(JUDGE_COHORT, JUDGE_SINCE)

    assert [(c.decision.event.decision_id, c.recent_gated) for c in candidates] == [
        (boundary.decision_id, True)
    ]


def test_judge_candidates_excludes_decisions_with_any_successful_result_in_cohort(
    tmp_path: Path,
) -> None:
    """A failed judge result never satisfies calibration backlog, and a
    decision with a successful result anywhere in its history is excluded
    from the backlog even if a later attempt in the same cohort failed."""
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)
    far_past = datetime(2026, 1, 1, tzinfo=UTC)

    fail_only = _seed_decision_for_judge(
        repository,
        trace_id=JUDGE_FAIL_ONLY_TRACE_ID,
        decision_id=JUDGE_FAIL_ONLY_DECISION_ID,
        decided_at=far_past,
    )
    _seed_scored_feedback(
        repository,
        feedback_id="01ARZ3NDEKTSV4RRFFQ69G5FE2",
        decision_id=fail_only.decision_id,
        created_at=far_past,
        score=2,
        idempotency_key="judge-feedback-fail-only",
    )
    _insert_judge_eval_run(database, eval_run_id=JUDGE_EVAL_RUN_ID_FAIL_ONLY, run_at=far_past)
    _insert_judge_eval_result(
        database,
        eval_result_id=JUDGE_EVAL_RESULT_ID_FAIL_ONLY,
        eval_run_id=JUDGE_EVAL_RUN_ID_FAIL_ONLY,
        decision_id=fail_only.decision_id,
        run_at=far_past,
        score=None,
    )

    success_then_fail = _seed_decision_for_judge(
        repository,
        trace_id=JUDGE_SUCCESS_THEN_FAIL_TRACE_ID,
        decision_id=JUDGE_SUCCESS_THEN_FAIL_DECISION_ID,
        decided_at=far_past,
    )
    _seed_scored_feedback(
        repository,
        feedback_id="01ARZ3NDEKTSV4RRFFQ69G5FE3",
        decision_id=success_then_fail.decision_id,
        created_at=far_past,
        score=4,
        idempotency_key="judge-feedback-success-then-fail",
    )
    _insert_judge_eval_run(
        database, eval_run_id=JUDGE_EVAL_RUN_ID_SUCCESS_THEN_FAIL_OLD, run_at=far_past
    )
    _insert_judge_eval_result(
        database,
        eval_result_id=JUDGE_EVAL_RESULT_ID_SUCCESS_THEN_FAIL_OLD,
        eval_run_id=JUDGE_EVAL_RUN_ID_SUCCESS_THEN_FAIL_OLD,
        decision_id=success_then_fail.decision_id,
        run_at=far_past,
        score=4,
    )
    _insert_judge_eval_run(
        database,
        eval_run_id=JUDGE_EVAL_RUN_ID_SUCCESS_THEN_FAIL_NEW,
        run_at=far_past + timedelta(days=1),
    )
    _insert_judge_eval_result(
        database,
        eval_result_id=JUDGE_EVAL_RESULT_ID_SUCCESS_THEN_FAIL_NEW,
        eval_run_id=JUDGE_EVAL_RUN_ID_SUCCESS_THEN_FAIL_NEW,
        decision_id=success_then_fail.decision_id,
        run_at=far_past + timedelta(days=1),
        score=None,
    )

    fail_then_success = _seed_decision_for_judge(
        repository,
        trace_id=JUDGE_FAIL_THEN_SUCCESS_TRACE_ID,
        decision_id=JUDGE_FAIL_THEN_SUCCESS_DECISION_ID,
        decided_at=far_past,
    )
    _seed_scored_feedback(
        repository,
        feedback_id="01ARZ3NDEKTSV4RRFFQ69G5FE4",
        decision_id=fail_then_success.decision_id,
        created_at=far_past,
        score=5,
        idempotency_key="judge-feedback-fail-then-success",
    )
    _insert_judge_eval_run(
        database, eval_run_id=JUDGE_EVAL_RUN_ID_FAIL_THEN_SUCCESS_OLD, run_at=far_past
    )
    _insert_judge_eval_result(
        database,
        eval_result_id=JUDGE_EVAL_RESULT_ID_FAIL_THEN_SUCCESS_OLD,
        eval_run_id=JUDGE_EVAL_RUN_ID_FAIL_THEN_SUCCESS_OLD,
        decision_id=fail_then_success.decision_id,
        run_at=far_past,
        score=None,
    )
    _insert_judge_eval_run(
        database,
        eval_run_id=JUDGE_EVAL_RUN_ID_FAIL_THEN_SUCCESS_NEW,
        run_at=far_past + timedelta(days=1),
    )
    _insert_judge_eval_result(
        database,
        eval_result_id=JUDGE_EVAL_RESULT_ID_FAIL_THEN_SUCCESS_NEW,
        eval_run_id=JUDGE_EVAL_RUN_ID_FAIL_THEN_SUCCESS_NEW,
        decision_id=fail_then_success.decision_id,
        run_at=far_past + timedelta(days=1),
        score=5,
    )

    candidates = repository.judge_candidates(JUDGE_COHORT, JUDGE_SINCE)

    assert [(c.decision.event.decision_id, c.calibration_backlog) for c in candidates] == [
        (fail_only.decision_id, True)
    ]


def test_judge_calibration_pairs_selects_newest_human_and_judge_score_per_decision(
    tmp_path: Path,
) -> None:
    """The calibration pair set is aggregated across runs: for each qualifying
    decision it pairs the newest human score with the newest successful judge
    result in this exact cohort, ignoring decisions missing either side, a
    different rubric version, a different cohort, or only failed results."""
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)
    far_past = datetime(2026, 1, 1, tzinfo=UTC)
    other_cohort = JudgeCohort(
        provider="claude", model="claude-3-5-sonnet", rubric_version="reasoning_quality_v1"
    )

    # Qualifies: newest human score (5, superseding an older 2) paired with
    # the newest successful judge score (4, superseding an older 2) -- a
    # re-run replaces the calibration observation rather than adding to it.
    paired = _seed_decision_for_judge(
        repository,
        trace_id="01ARZ3NDEKTSV4RRFFQ69G5FF0",
        decision_id="01ARZ3NDEKTSV4RRFFQ69G5FF1",
        decided_at=far_past,
    )
    _seed_scored_feedback(
        repository,
        feedback_id="01ARZ3NDEKTSV4RRFFQ69G5FF2",
        decision_id=paired.decision_id,
        created_at=far_past,
        score=2,
        idempotency_key="calibration-pairs-old-human",
    )
    _seed_scored_feedback(
        repository,
        feedback_id="01ARZ3NDEKTSV4RRFFQ69G5FF3",
        decision_id=paired.decision_id,
        created_at=far_past + timedelta(days=2),
        score=5,
        idempotency_key="calibration-pairs-new-human",
    )
    _insert_judge_eval_run(database, eval_run_id="01ARZ3NDEKTSV4RRFFQ69G5FF4", run_at=far_past)
    _insert_judge_eval_result(
        database,
        eval_result_id="01ARZ3NDEKTSV4RRFFQ69G5FF5",
        eval_run_id="01ARZ3NDEKTSV4RRFFQ69G5FF4",
        decision_id=paired.decision_id,
        run_at=far_past,
        score=2,
    )
    _insert_judge_eval_run(
        database, eval_run_id="01ARZ3NDEKTSV4RRFFQ69G5FF6", run_at=far_past + timedelta(days=1)
    )
    _insert_judge_eval_result(
        database,
        eval_result_id="01ARZ3NDEKTSV4RRFFQ69G5FF7",
        eval_run_id="01ARZ3NDEKTSV4RRFFQ69G5FF6",
        decision_id=paired.decision_id,
        run_at=far_past + timedelta(days=1),
        score=4,
    )

    # Excluded: human score present, but only a failed judge result in this
    # cohort (no successful result to pair with).
    human_only = _seed_decision_for_judge(
        repository,
        trace_id="01ARZ3NDEKTSV4RRFFQ69G5FF8",
        decision_id="01ARZ3NDEKTSV4RRFFQ69G5FF9",
        decided_at=far_past,
    )
    _seed_scored_feedback(
        repository,
        feedback_id="01ARZ3NDEKTSV4RRFFQ69G5FFA",
        decision_id=human_only.decision_id,
        created_at=far_past,
        score=3,
        idempotency_key="calibration-pairs-human-only",
    )
    _insert_judge_eval_run(database, eval_run_id="01ARZ3NDEKTSV4RRFFQ69G5FFB", run_at=far_past)
    _insert_judge_eval_result(
        database,
        eval_result_id="01ARZ3NDEKTSV4RRFFQ69G5FFC",
        eval_run_id="01ARZ3NDEKTSV4RRFFQ69G5FFB",
        decision_id=human_only.decision_id,
        run_at=far_past,
        score=None,
    )

    # Excluded: a successful judge result exists, but in a different
    # provider/model cohort than the one being queried.
    other_cohort_decision = _seed_decision_for_judge(
        repository,
        trace_id="01ARZ3NDEKTSV4RRFFQ69G5FFD",
        decision_id="01ARZ3NDEKTSV4RRFFQ69G5FFE",
        decided_at=far_past,
    )
    _seed_scored_feedback(
        repository,
        feedback_id="01ARZ3NDEKTSV4RRFFQ69G5FFF",
        decision_id=other_cohort_decision.decision_id,
        created_at=far_past,
        score=1,
        idempotency_key="calibration-pairs-other-cohort",
    )
    _insert_judge_eval_run(
        database,
        eval_run_id="01ARZ3NDEKTSV4RRFFQ69G5FFG",
        run_at=far_past,
        cohort=other_cohort,
    )
    _insert_judge_eval_result(
        database,
        eval_result_id="01ARZ3NDEKTSV4RRFFQ69G5FFH",
        eval_run_id="01ARZ3NDEKTSV4RRFFQ69G5FFG",
        decision_id=other_cohort_decision.decision_id,
        run_at=far_past,
        score=1,
    )

    # Excluded: a successful judge result exists in this cohort, but there is
    # no human score at all for this rubric version.
    judge_only = _seed_decision_for_judge(
        repository,
        trace_id="01ARZ3NDEKTSV4RRFFQ69G5FFJ",
        decision_id="01ARZ3NDEKTSV4RRFFQ69G5FFK",
        decided_at=far_past,
    )
    _insert_judge_eval_run(database, eval_run_id="01ARZ3NDEKTSV4RRFFQ69G5FFM", run_at=far_past)
    _insert_judge_eval_result(
        database,
        eval_result_id="01ARZ3NDEKTSV4RRFFQ69G5FFN",
        eval_run_id="01ARZ3NDEKTSV4RRFFQ69G5FFM",
        decision_id=judge_only.decision_id,
        run_at=far_past,
        score=3,
    )

    pairs = repository.judge_calibration_pairs(JUDGE_COHORT)

    assert pairs == ((5, 4),)


def test_judge_calibration_pairs_returns_empty_tuple_when_no_decision_qualifies(
    tmp_path: Path,
) -> None:
    repository = Repository(Database.open(tmp_path / "glassbox.sqlite3"))

    assert repository.judge_calibration_pairs(JUDGE_COHORT) == ()


def test_judge_calibration_pairs_exclude_decision_ids_drops_a_qualifying_pair(
    tmp_path: Path,
) -> None:
    """Regression test for the P3a review fix: ``exclude_decision_ids`` drops
    a decision's pair entirely from the result, regardless of what it would
    otherwise compute for it. ``glassbox.eval.judge.run_judge()`` uses this
    to keep a decision's stale, already-committed pair out of its own
    pre-persist calibration read when that same decision was just re-judged
    successfully within the same invocation (see judge.py's
    ``_combine_calibration_pairs`` docstring)."""
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)
    far_past = datetime(2026, 1, 1, tzinfo=UTC)

    excluded = _seed_decision_for_judge(
        repository,
        trace_id="01ARZ3NDEKTSV4RRFFQ69G5FG0",
        decision_id="01ARZ3NDEKTSV4RRFFQ69G5FG1",
        decided_at=far_past,
    )
    _seed_scored_feedback(
        repository,
        feedback_id="01ARZ3NDEKTSV4RRFFQ69G5FG2",
        decision_id=excluded.decision_id,
        created_at=far_past,
        score=5,
        idempotency_key="calibration-pairs-exclude-human",
    )
    _insert_judge_eval_run(database, eval_run_id="01ARZ3NDEKTSV4RRFFQ69G5FG3", run_at=far_past)
    _insert_judge_eval_result(
        database,
        eval_result_id="01ARZ3NDEKTSV4RRFFQ69G5FG4",
        eval_run_id="01ARZ3NDEKTSV4RRFFQ69G5FG3",
        decision_id=excluded.decision_id,
        run_at=far_past,
        score=1,
    )

    kept = _seed_decision_for_judge(
        repository,
        trace_id="01ARZ3NDEKTSV4RRFFQ69G5FG5",
        decision_id="01ARZ3NDEKTSV4RRFFQ69G5FG6",
        decided_at=far_past,
    )
    _seed_scored_feedback(
        repository,
        feedback_id="01ARZ3NDEKTSV4RRFFQ69G5FG7",
        decision_id=kept.decision_id,
        created_at=far_past,
        score=3,
        idempotency_key="calibration-pairs-exclude-kept-human",
    )
    _insert_judge_eval_run(database, eval_run_id="01ARZ3NDEKTSV4RRFFQ69G5FG8", run_at=far_past)
    _insert_judge_eval_result(
        database,
        eval_result_id="01ARZ3NDEKTSV4RRFFQ69G5FG9",
        eval_run_id="01ARZ3NDEKTSV4RRFFQ69G5FG8",
        decision_id=kept.decision_id,
        run_at=far_past,
        score=3,
    )

    # decision_id ordering: "...FG1" sorts before "...FG6".
    assert repository.judge_calibration_pairs(JUDGE_COHORT) == ((5, 1), (3, 3))

    filtered = repository.judge_calibration_pairs(
        JUDGE_COHORT, exclude_decision_ids=frozenset({excluded.decision_id})
    )

    assert filtered == ((3, 3),)

    # An empty exclude set (the default) is a no-op, not an always-empty filter.
    assert repository.judge_calibration_pairs(JUDGE_COHORT, exclude_decision_ids=frozenset()) == (
        (5, 1),
        (3, 3),
    )


def test_record_judge_run_persists_run_and_results_with_correct_nullability_and_flags(
    tmp_path: Path,
) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)
    ok_decision = _seed_decision_for_judge(
        repository,
        trace_id=JUDGE_OK_TRACE_ID,
        decision_id=JUDGE_OK_DECISION_ID,
        decided_at=datetime(2026, 6, 10, tzinfo=UTC),
    )
    failed_decision = _seed_decision_for_judge(
        repository,
        trace_id=JUDGE_FAILED_TRACE_ID,
        decision_id=JUDGE_FAILED_DECISION_ID,
        decided_at=datetime(2026, 6, 11, tzinfo=UTC),
    )
    run = JudgeRun(
        eval_run_id=JUDGE_RUN_ID,
        cohort=JUDGE_COHORT,
        run_at=datetime(2026, 6, 12, tzinfo=UTC),
        status="passed",
        status_reason="calibration gate satisfied",
        judge_failure_count=1,
        self_judge_allowed=False,
    )
    results = (
        JudgeOutcome(
            decision_id=ok_decision.decision_id,
            score=4,
            rationale="Well-grounded in the cited evidence.",
            error=None,
            self_judge_bypassed=False,
        ),
        JudgeOutcome(
            decision_id=failed_decision.decision_id,
            score=None,
            rationale=None,
            error="judge response was not valid JSON",
            self_judge_bypassed=True,
        ),
    )

    repository.record_judge_run(run, results)

    run_row = database.connection.execute(
        "SELECT * FROM eval_runs WHERE eval_run_id = ?", (JUDGE_RUN_ID,)
    ).fetchone()
    assert run_row is not None
    assert run_row["run_kind"] == "judge"
    assert run_row["judge_provider"] == "openai"
    assert run_row["judge_model"] == "gpt-4o"
    assert run_row["rubric_version"] == "reasoning_quality_v1"
    assert run_row["judge_temperature"] == 0
    assert run_row["self_judge_allowed"] == 0
    assert run_row["status"] == "passed"
    assert run_row["status_reason"] == "calibration gate satisfied"
    assert run_row["judge_failure_count"] == 1

    result_rows = {
        row["decision_id"]: row
        for row in database.connection.execute(
            "SELECT * FROM eval_results WHERE eval_run_id = ?", (JUDGE_RUN_ID,)
        )
    }
    ok_row = result_rows[ok_decision.decision_id]
    assert ok_row["score"] == 4
    assert ok_row["judge_rationale"] == "Well-grounded in the cited evidence."
    assert ok_row["passed"] == 1
    assert ok_row["self_judge_bypassed"] == 0
    assert ok_row["assertion_name"] == "reasoning_quality"
    assert ok_row["case_id"] == ok_decision.decision_id

    failed_row = result_rows[failed_decision.decision_id]
    assert failed_row["score"] is None
    assert failed_row["judge_rationale"] == "judge response was not valid JSON"
    assert failed_row["passed"] == 0
    assert failed_row["self_judge_bypassed"] == 1


def test_record_judge_run_rejects_a_successful_outcome_carrying_an_error(tmp_path: Path) -> None:
    repository = Repository(Database.open(tmp_path / "glassbox.sqlite3"))
    run = JudgeRun(
        eval_run_id=JUDGE_RUN_ID,
        cohort=JUDGE_COHORT,
        run_at=datetime(2026, 6, 12, tzinfo=UTC),
        status="passed",
        status_reason="calibration gate satisfied",
        judge_failure_count=0,
        self_judge_allowed=False,
    )
    outcome = JudgeOutcome(
        decision_id=DECISION_ID,
        score=4,
        rationale="ok",
        error="unexpected error",
        self_judge_bypassed=False,
    )

    with pytest.raises(ValueError, match="score"):
        repository.record_judge_run(run, (outcome,))


def test_record_judge_run_rejects_an_unsupported_status(tmp_path: Path) -> None:
    repository = Repository(Database.open(tmp_path / "glassbox.sqlite3"))
    run = JudgeRun(
        eval_run_id=JUDGE_RUN_ID,
        cohort=JUDGE_COHORT,
        run_at=datetime(2026, 6, 12, tzinfo=UTC),
        status="passed",
        status_reason="calibration gate satisfied",
        judge_failure_count=0,
        self_judge_allowed=False,
    )
    invalid_run = dataclasses.replace(run, status="bogus")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="status"):
        repository.record_judge_run(invalid_run, ())


def test_record_judge_run_is_atomic_when_a_result_violates_a_foreign_key(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    repository = Repository(database)
    run = JudgeRun(
        eval_run_id=JUDGE_RUN_ID,
        cohort=JUDGE_COHORT,
        run_at=datetime(2026, 6, 12, tzinfo=UTC),
        status="failed",
        status_reason="all cases errored",
        judge_failure_count=1,
        self_judge_allowed=False,
    )
    outcome = JudgeOutcome(
        decision_id=JUDGE_MISSING_DECISION_ID,
        score=None,
        rationale=None,
        error="boom",
        self_judge_bypassed=False,
    )

    with pytest.raises(sqlite3.IntegrityError):
        repository.record_judge_run(run, (outcome,))

    assert database.connection.execute("SELECT COUNT(*) FROM eval_runs").fetchone()[0] == 0
    assert database.connection.execute("SELECT COUNT(*) FROM eval_results").fetchone()[0] == 0
