"""Static snapshot assembly stays within one strict read-only transaction."""

import sqlite3
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, NoReturn

import pytest

from glassbox import dashboard
from glassbox.dashboard import build_dashboard
from glassbox.eval import drift, reconciliation
from glassbox.events import DecisionEvent, TraceEvent
from glassbox.store import Database, ReadOnlyDatabaseError, Repository
from glassbox.store.repository import CounterfactualRun, OutcomeSubmission

AS_OF = datetime(2026, 11, 6, 12, tzinfo=UTC)
DECIDED = AS_OF - timedelta(days=40)
PRIVATE = "private-decision-content"


def _id(index: int) -> str:
    return f"{index:026d}"


def _decision(repository: Repository, index: int, agent: str, kind: str = "triage",
              *, decided_at: datetime = DECIDED) -> None:
    repository.write_event(TraceEvent(
        trace_id=_id(index), agent_name=agent, agent_version="v1",
        started_at=decided_at, environment="dev", input_ref="private-blob",
    ))
    repository.write_event(DecisionEvent(
        decision_id=_id(index + 1000), trace_id=_id(index), agent_name=agent,
        agent_version="v1", entity_type="sku", entity_id=PRIVATE, decision_type=kind,
        recommendation={"urgency": "HIGH", "secret": PRIVATE}, rationale=PRIVATE,
        rationale_citations=(), confidence=0.8, alternatives_considered=(),
        decided_at=decided_at,
    ))


def _outcome(repository: Repository, index: int,
             label: Literal["tp", "fp", "tn", "fn"] = "tp") -> None:
    policy = reconciliation.load_policy(
        reconciliation.default_policy_path().with_name("reconciliation_v2.toml")
    )
    repository.record_outcome(OutcomeSubmission(
        outcome_id=_id(index + 2000), source_id=f"private-source-{index}",
        decision_id=_id(index + 1000), outcome_type="stockout_occurred",
        observed_at=DECIDED + timedelta(days=30), horizon_days=30,
        value={"occurred": True, "secret": PRIVATE}, label=label,
        reconciliation_policy_version=policy.policy_version,
        reconciliation_policy_hash=policy.policy_hash,
    ))


@pytest.fixture
def empty_path(tmp_path: Path) -> Path:
    path = tmp_path / "glassbox.sqlite3"
    Database.open(path).close()
    return path


@pytest.fixture
def populated_path(empty_path: Path) -> Path:
    database = Database.open(empty_path)
    try:
        repository = Repository(database)
        # Seed in reverse cohort order and materialize the default policy baseline.
        _decision(repository, 1, "agent-b")
        for index in range(2, 502):
            _decision(repository, index, "agent-a")
        _outcome(repository, 2)
        for index in range(21):
            repository.record_counterfactual_run(CounterfactualRun(
                run_id=_id(index + 4000), created_at=AS_OF - timedelta(hours=index // 2),
                as_of=DECIDED, decided_since=DECIDED, provider="test-provider",
                model="test-model", base_url="https://private-host/secret-token",
                prompt_version="prompt-v1", prompt_hash="a" * 64,
                outcome_policy_version="reconciliation_v2", outcome_policy_hash="b" * 64,
                selected_count=0, succeeded_count=0, failed_count=0,
                remote_egress_confirmed=False,
                execution_mode="live_shadow" if index == 0 else "offline",
            ), ())
    finally:
        database.close()
    drift.create_baseline(
        empty_path, "agent-a", drift.load_policy(drift.default_policy_path()),
        supersede=False, clock=lambda: AS_OF,
        baseline_start=DECIDED - timedelta(days=1), baseline_end=DECIDED + timedelta(days=1),
    )
    return empty_path


def test_dashboard_uses_one_as_of_and_orders_cohorts_and_runs(
    populated_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    instants: list[datetime] = []
    repositories: list[Repository] = []
    original_drift = dashboard.run_live_drift_report
    original_report = dashboard.calculate_report
    clock_calls = 0

    def clock() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        return AS_OF

    def live(repository, agent, policy, *, as_of):  # type: ignore[no-untyped-def]
        instants.append(as_of)
        repositories.append(repository)
        return original_drift(repository, agent, policy, as_of=as_of)

    def report(policy, source, *, as_of):  # type: ignore[no-untyped-def]
        instants.append(as_of)
        return original_report(policy, source, as_of=as_of)

    monkeypatch.setattr(dashboard, "run_live_drift_report", live)
    monkeypatch.setattr(dashboard, "calculate_report", report)
    view = build_dashboard(populated_path, clock=clock)

    assert clock_calls == 1
    assert instants == [AS_OF, AS_OF, AS_OF]
    assert repositories[0] is repositories[1]
    assert view.generated_at == "2026-11-06T12:00:00Z"
    assert view.drift.state == "ready"
    assert [item.agent_name for item in view.drift.cohorts] == ["agent-a", "agent-b"]
    assert all(item.calculated_at == view.generated_at for item in view.drift.cohorts)
    assert view.drift.cohorts[0].baseline_id is not None
    assert view.drift.cohorts[1].baseline_id is None
    assert view.drift.cohorts[1].status == "Insufficient Data"
    assert any(not signal.monitored for signal in view.drift.cohorts[0].signals)
    assert len(view.counterfactuals.runs) == 20
    assert view.counterfactuals.has_older_runs is True
    assert [item.run_id for item in view.counterfactuals.runs] == [
        _id(4000 + index) for pair in range(10) for index in (pair * 2 + 1, pair * 2)
    ]


def test_dashboard_opens_once_begins_before_sources_and_closes(
    populated_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = Database.open_read_only
    connections: list[sqlite3.Connection] = []
    statements: list[str] = []

    def open_once(path: Path) -> Database:
        assert not connections
        database = original_open(path)
        connections.append(database.connection)
        database.connection.set_trace_callback(statements.append)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            database.connection.execute("DELETE FROM outcomes")
        database.connection.rollback()
        statements.clear()
        return database

    monkeypatch.setattr(Database, "open_read_only", open_once)
    build_dashboard(populated_path, clock=lambda: AS_OF)

    assert len(connections) == 1
    assert statements[0] == "BEGIN"
    assert all(sql.lstrip().upper().startswith(("SELECT", "WITH", "BEGIN"))
               for sql in statements)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


def _forbidden(*args: object, **kwargs: object) -> NoReturn:
    raise AssertionError("dashboard invoked an active subsystem")


def test_dashboard_never_writes_or_invokes_active_subsystems(
    populated_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from glassbox.eval import counterfactual_provider, judge_provider
    from glassbox.ops import scheduler, scheduler_runner
    from glassbox.store import database as database_module

    monkeypatch.setattr(Database, "open", _forbidden)
    monkeypatch.setattr(database_module, "_initialize_schema", _forbidden)
    for name in ("record_drift_run", "claim_discord_delivery", "record_counterfactual_run"):
        monkeypatch.setattr(Repository, name, _forbidden)
    for name in ("install_drift_schedule", "drift_schedule_status", "remove_drift_schedule"):
        monkeypatch.setattr(scheduler, name, _forbidden)
    monkeypatch.setattr(scheduler_runner, "main", _forbidden)
    monkeypatch.setattr(judge_provider, "create_judge_provider", _forbidden)
    monkeypatch.setattr(counterfactual_provider, "create_counterfactual_provider", _forbidden)
    monkeypatch.setattr(counterfactual_provider, "create_judge_provider", _forbidden)

    assert build_dashboard(populated_path, clock=lambda: AS_OF).drift.state == "ready"


def test_sections_share_a_snapshot_when_another_connection_commits(
    populated_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = Database.open(populated_path)
    writer_repository = Repository(writer)
    original_drift = dashboard.run_live_drift_report
    injected = False

    def live(repository, agent, policy, *, as_of):  # type: ignore[no-untyped-def]
        nonlocal injected
        report = original_drift(repository, agent, policy, as_of=as_of)
        if not injected:
            injected = True
            _decision(writer_repository, 600, "replenishment-triage")
        return report

    monkeypatch.setattr(dashboard, "run_live_drift_report", live)
    try:
        view = build_dashboard(populated_path, clock=lambda: AS_OF)
        assert injected
        assert view.reconciliation.total_decisions == 501
        assert writer.connection.execute("SELECT count(*) FROM decisions").fetchone()[0] == 502
        refreshed = build_dashboard(populated_path, clock=lambda: AS_OF)
        assert refreshed.reconciliation.total_decisions == 502
    finally:
        writer.close()


def test_dashboard_models_only_contain_safe_immutable_presentation_data(
    populated_path: Path,
) -> None:
    view = build_dashboard(populated_path, clock=lambda: AS_OF)
    serialized = repr(asdict(view))
    for secret in (PRIVATE, "private-blob", "private-source", "private-host", "secret-token",
                   "base_url", "recommendation", "rationale"):
        assert secret not in serialized
    with pytest.raises(FrozenInstanceError):
        view.generated_at = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        view.counterfactuals.runs[0].provider = "changed"  # type: ignore[misc]


def test_dashboard_empty_sections_are_normal(empty_path: Path) -> None:
    view = build_dashboard(empty_path, clock=lambda: AS_OF)
    assert (view.drift.state, view.drift.message, view.drift.cohorts) == (
        "empty", "No recorded decision or trace activity.", (),
    )
    assert (view.reconciliation.state, view.reconciliation.message) == (
        "empty", "No outcomes have been imported.",
    )
    assert "Negative outcomes must be exported and imported explicitly" in (
        view.reconciliation.outcome_notice
    )
    assert (view.counterfactuals.state, view.counterfactuals.message,
            view.counterfactuals.runs, view.counterfactuals.has_older_runs) == (
        "empty", "No counterfactual runs have been recorded.", (), False,
    )


def test_dashboard_reconciliation_uses_v2_and_sorted_decision_types(empty_path: Path) -> None:
    database = Database.open(empty_path)
    try:
        repository = Repository(database)
        for index, kind in enumerate(("zebra", "triage", "alpha", "triage"), start=1):
            _decision(repository, index, "replenishment-triage", kind)
        _decision(repository, 5, "replenishment-triage", decided_at=AS_OF)
        _outcome(repository, 2, "tp")
        _outcome(repository, 4, "fn")
    finally:
        database.close()

    view = build_dashboard(empty_path, clock=lambda: AS_OF).reconciliation
    policy = reconciliation.load_policy(
        reconciliation.default_policy_path().with_name("reconciliation_v2.toml")
    )
    assert view.state == "ready"
    assert (view.policy_version, view.policy_hash) == (policy.policy_version, policy.policy_hash)
    assert [row.decision_type for row in view.by_decision_type] == ["alpha", "triage", "zebra"]
    assert (view.total_decisions, view.immature_count, view.maturity_days) == (5, 1, 30)
    assert (view.totals.mature, view.totals.labelled, view.totals.unlabelled) == (4, 2, 2)
    assert (view.totals.tp, view.totals.fp, view.totals.tn, view.totals.fn) == (1, 0, 0, 1)
    assert view.totals.precision == 1.0
    assert view.totals.recall == 0.5
    assert view.totals.f1 == pytest.approx(2 / 3)


def test_reconciliation_without_matching_labels_is_not_no_outcomes(populated_path: Path) -> None:
    section = build_dashboard(populated_path, clock=lambda: AS_OF).reconciliation
    assert section.state == "ready"
    assert section.totals.labelled == 0
    assert section.totals.precision is None
    assert section.totals.recall is None
    assert section.totals.f1 is None


@pytest.mark.parametrize("section,method,error", [
    ("drift", "run_live_drift_report", drift.DriftPolicyError),
    ("reconciliation", "calculate_report", reconciliation.ReconciliationError),
])
def test_report_policy_errors_only_make_their_section_unavailable(
    populated_path: Path, monkeypatch: pytest.MonkeyPatch, section: str, method: str,
    error: type[ValueError],
) -> None:
    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise error("private-exception-details")

    monkeypatch.setattr(dashboard, method, fail)
    view = build_dashboard(populated_path, clock=lambda: AS_OF)
    failed = getattr(view, section)
    assert (failed.state, failed.message) == ("unavailable", "This section is unavailable.")
    assert "private-exception-details" not in repr(view)
    assert view.counterfactuals.state == "ready"


def test_malformed_counterfactual_is_unavailable(
    populated_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_runs = Repository.counterfactual_runs

    def malformed(repository: Repository, limit: int) -> tuple[CounterfactualRun, ...]:
        assert limit == 21
        runs = original_runs(repository, limit)
        return (replace(runs[0], created_at=datetime(2026, 1, 1)),)

    monkeypatch.setattr(Repository, "counterfactual_runs", malformed)
    view = build_dashboard(populated_path, clock=lambda: AS_OF)
    assert (view.counterfactuals.state, view.counterfactuals.message) == (
        "unavailable", "This section is unavailable.",
    )
    assert view.counterfactuals.runs == ()
    assert view.drift.state == "ready"


def test_twenty_counterfactual_runs_do_not_claim_older_history(
    populated_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_runs = Repository.counterfactual_runs

    def exactly_twenty(repository: Repository, limit: int) -> tuple[CounterfactualRun, ...]:
        assert limit == 21
        return original_runs(repository, 20)

    monkeypatch.setattr(Repository, "counterfactual_runs", exactly_twenty)
    section = build_dashboard(populated_path, clock=lambda: AS_OF).counterfactuals
    assert len(section.runs) == 20
    assert section.has_older_runs is False


@pytest.mark.parametrize("instant", [
    datetime(2026, 11, 6), datetime(2026, 11, 6, tzinfo=timezone(timedelta(hours=1))), None,
])
def test_invalid_clock_is_rejected_before_database_open(
    empty_path: Path, monkeypatch: pytest.MonkeyPatch, instant: datetime,
) -> None:
    monkeypatch.setattr(Database, "open_read_only", _forbidden)
    with pytest.raises(ValueError):
        build_dashboard(empty_path, clock=lambda: instant)


def test_missing_database_error_propagates(tmp_path: Path) -> None:
    with pytest.raises(ReadOnlyDatabaseError):
        build_dashboard(tmp_path / "missing.sqlite3", clock=lambda: AS_OF)


def test_sqlite_errors_propagate_and_connection_is_closed(
    empty_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_open = Database.open_read_only
    connections: list[sqlite3.Connection] = []

    def capture(path: Path) -> Database:
        database = original_open(path)
        connections.append(database.connection)
        return database

    def fail(repository: Repository) -> NoReturn:
        raise sqlite3.OperationalError("private-sqlite-error")

    monkeypatch.setattr(Database, "open_read_only", capture)
    monkeypatch.setattr(Repository, "drift_agent_names", fail)
    with pytest.raises(sqlite3.OperationalError):
        build_dashboard(empty_path, clock=lambda: AS_OF)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")
