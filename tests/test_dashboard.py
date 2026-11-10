"""Static snapshot assembly stays within one strict read-only transaction."""

import sqlite3
import stat
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


@pytest.mark.parametrize("field,value", [
    ("prompt_hash", b"a" * 64),
    ("outcome_policy_hash", b"b" * 64),
    ("run_id", b"00000000000000000000004999"),
])
def test_persisted_binary_counterfactual_provenance_is_unavailable(
    populated_path: Path, field: str, value: bytes,
) -> None:
    database = Database.open(populated_path)
    try:
        row = dict(database.connection.execute(
            "SELECT * FROM counterfactual_runs LIMIT 1"
        ).fetchone())
        row["run_id"] = _id(4999)
        row[field] = value
        columns = ",".join(row)
        placeholders = ",".join("?" for _ in row)
        database.connection.execute(
            f"INSERT INTO counterfactual_runs ({columns}) VALUES ({placeholders})",
            tuple(row.values()),
        )
        database.connection.commit()
    finally:
        database.close()

    view = build_dashboard(populated_path, clock=lambda: AS_OF)

    assert (view.counterfactuals.state, view.counterfactuals.message) == (
        "unavailable", "This section is unavailable.",
    )
    assert view.counterfactuals.runs == ()
    assert view.drift.state == view.reconciliation.state == "ready"


@pytest.mark.parametrize("field", ["created_at", "as_of", "decided_since"])
@pytest.mark.parametrize("value", ["0000-01-01T00:00:00Z", b"2026-11-06T12:00:00Z"])
def test_persisted_malformed_counterfactual_timestamps_only_disable_history(
    populated_path: Path, capsys, field: str, value: str | bytes,
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main

    database = Database.open(populated_path)
    try:
        row = dict(database.connection.execute(
            "SELECT * FROM counterfactual_runs LIMIT 1"
        ).fetchone())
        row["run_id"] = _id(4999)
        row[field] = value
        # Model corrupted persisted data without changing the released schema.
        database.connection.execute("PRAGMA ignore_check_constraints = ON")
        columns = ",".join(row)
        placeholders = ",".join("?" for _ in row)
        database.connection.execute(
            f"INSERT INTO counterfactual_runs ({columns}) VALUES ({placeholders})",
            tuple(row.values()),
        )
        database.connection.commit()
    finally:
        database.close()

    view = build_dashboard(populated_path, clock=lambda: AS_OF)

    assert (view.counterfactuals.state, view.counterfactuals.message) == (
        "unavailable", "This section is unavailable.",
    )
    assert view.counterfactuals.runs == ()
    assert view.drift.state == view.reconciliation.state == "ready"
    output = populated_path.with_suffix(".html")
    assert main([
        "--database", str(populated_path), "dashboard", "--output", str(output),
    ]) == 0
    html = output.read_text(encoding="utf-8")
    assert html.count("This section is unavailable.") == 1
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


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


@pytest.mark.parametrize("method", ["drift_agent_names", "counterfactual_runs"])
def test_sqlite_errors_propagate_and_connection_is_closed(
    empty_path: Path, monkeypatch: pytest.MonkeyPatch, method: str,
) -> None:
    original_open = Database.open_read_only
    connections: list[sqlite3.Connection] = []

    def capture(path: Path) -> Database:
        database = original_open(path)
        connections.append(database.connection)
        return database

    def fail(repository: Repository, *args: object) -> NoReturn:
        raise sqlite3.OperationalError("private-sqlite-error")

    monkeypatch.setattr(Database, "open_read_only", capture)
    monkeypatch.setattr(Repository, method, fail)
    with pytest.raises(sqlite3.OperationalError):
        build_dashboard(empty_path, clock=lambda: AS_OF)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


def test_render_is_standalone_escaped_and_excludes_source_fields(populated_path: Path) -> None:
    view = build_dashboard(populated_path, clock=lambda: AS_OF)
    untrusted = "<script>UNTRUSTED</script>"
    cohort = replace(view.drift.cohorts[0], agent_name=untrusted, policy_version=untrusted)
    run = replace(view.counterfactuals.runs[0], model=untrusted)
    # Even an excluded source field injected into a display object is never rendered.
    object.__setattr__(run, "base_url", "https://private.example/token")
    object.__setattr__(run, "rationale", "PRIVATE_COUNTERFACTUAL_RATIONALE")
    view = replace(
        view, drift=replace(view.drift, cohorts=(cohort, view.drift.cohorts[1])),
        counterfactuals=replace(view.counterfactuals, runs=(run,)),
    )

    html = dashboard.render_dashboard(view)

    assert html.lstrip().startswith("<!doctype html>")
    assert '<meta charset="utf-8">' in html
    assert "<style>" in html
    css = Path(dashboard.__file__).parent / "web/static/glassbox.css"
    assert css.read_text(encoding="utf-8") in html
    assert "&lt;script&gt;UNTRUSTED&lt;/script&gt;" in html
    for forbidden in ("<script", "<form", "href=", "src=", "<link", "http://", "https://",
                      "PRIVATE_COUNTERFACTUAL_RATIONALE", "private.example", PRIVATE,
                      "private-blob", "private-host"):
        assert forbidden not in html
    assert "Not monitored (disabled by policy)" in html
    assert html == dashboard.render_dashboard(view)


def test_render_ready_sections_show_operational_summaries(populated_path: Path) -> None:
    view = build_dashboard(populated_path, clock=lambda: AS_OF)

    html = dashboard.render_dashboard(view)

    for text in (
        "Generated at", view.generated_at, "Report format version: 1", "static snapshot",
        "Drift overview", "Outcome reconciliation", "Counterfactual history",
        "Insufficient Data", "Not materialized", view.drift.cohorts[0].action,
        view.drift.cohorts[0].policy_hash, view.drift.cohorts[0].baseline_id,
        "Monitored signals", "Eligible decisions", "Labelled decisions", "Unlabelled",
        "Maturity window", "Immature decisions", "True positives", "False positives",
        "True negatives", "False negatives", "Precision", "Recall", "F1", "triage",
        view.reconciliation.policy_version, view.reconciliation.policy_hash,
        view.reconciliation.outcome_notice, "Newest 20 runs", "older runs",
        view.counterfactuals.runs[0].run_id, "test-provider", "test-model", "prompt-v1",
        "a" * 64, "b" * 64, "live_shadow", "Selected", "Succeeded", "Failed",
    ):
        assert text in html
    assert html.count('class="dashboard-section section-card"') == 3
    assert html.index(_id(4001)) < html.index(_id(4000)) < html.index(_id(4003))


def test_render_empty_and_unavailable_states(empty_path: Path) -> None:
    view = build_dashboard(empty_path, clock=lambda: AS_OF)

    html = dashboard.render_dashboard(view)

    for section in (view.drift, view.reconciliation, view.counterfactuals):
        assert section.message in html
    assert view.reconciliation.outcome_notice in html
    assert "Not available" in html  # Undefined metric denominators stay explicit.
    assert "Newest 20 runs" not in html
    unavailable = replace(
        view,
        drift=dashboard.DriftSectionView("unavailable", "This section is unavailable."),
        reconciliation=dashboard.ReconciliationSectionView(
            "unavailable", "This section is unavailable.",
        ),
        counterfactuals=dashboard.CounterfactualSectionView(
            "unavailable", "This section is unavailable.",
        ),
    )
    html = dashboard.render_dashboard(unavailable)
    assert html.count("Unavailable") == 3
    assert html.count("This section is unavailable.") == 3
    assert "None" not in html


def test_writer_refuses_existing_output_without_creating_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "report.html"
    target.write_bytes(b"old")
    monkeypatch.setattr(dashboard.tempfile, "NamedTemporaryFile", _forbidden)

    with pytest.raises(dashboard.DashboardError):
        dashboard.write_dashboard(target, "new", overwrite=False)

    assert target.read_bytes() == b"old"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("overwrite", [False, True])
def test_writer_publishes_complete_private_utf8_file(tmp_path: Path, overwrite: bool) -> None:
    target = tmp_path / "nested" / "report.html"
    if overwrite:
        target.parent.mkdir()
        target.write_bytes(b"old")
    html = "<!doctype html><html><body>Snapshot \u00b7 \u0394</body></html>"

    dashboard.write_dashboard(target, html, overwrite=overwrite)

    assert target.read_bytes() == html.encode("utf-8")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not list(target.parent.glob("*.tmp"))


def test_writer_fsyncs_private_same_directory_temp_before_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "report.html"
    target.write_bytes(b"old")
    html = "complete snapshot \u0394"
    fsynced = False
    original_fsync = dashboard.os.fsync
    original_replace = dashboard.os.replace

    def fsync(descriptor: int) -> None:
        nonlocal fsynced
        assert target.read_bytes() == b"old"
        original_fsync(descriptor)
        fsynced = True

    def publish(source: Path, destination: Path) -> None:
        source = Path(source)
        assert fsynced
        assert destination == target
        assert source.parent == target.parent
        assert source.name.startswith(f".{target.name}.")
        assert source.suffix == ".tmp"
        assert stat.S_IMODE(source.stat().st_mode) == 0o600
        assert source.read_bytes() == html.encode("utf-8")
        original_replace(source, destination)

    monkeypatch.setattr(dashboard.os, "fsync", fsync)
    monkeypatch.setattr(dashboard.os, "replace", publish)
    dashboard.write_dashboard(target, html, overwrite=True)
    assert target.read_text(encoding="utf-8") == html


@pytest.mark.parametrize("existing", [False, True])
def test_writer_replace_failure_preserves_target_and_cleans_only_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool,
) -> None:
    target = tmp_path / "report.html"
    if existing:
        target.write_bytes(b"old\x00\xff")
    unrelated = tmp_path / "unrelated.tmp"
    unrelated.write_bytes(b"keep")

    def fail(*args: object) -> NoReturn:
        raise OSError("replace failed")

    monkeypatch.setattr(dashboard.os, "replace", fail)
    with pytest.raises(OSError, match="replace failed"):
        dashboard.write_dashboard(target, "new", overwrite=True)

    if existing:
        assert target.read_bytes() == b"old\x00\xff"
    else:
        assert not target.exists()
    assert list(tmp_path.glob("*.tmp")) == [unrelated]
    assert unrelated.read_bytes() == b"keep"


def test_writer_no_overwrite_rejects_a_dangling_symlink(tmp_path: Path) -> None:
    target = tmp_path / "report.html"
    missing = tmp_path / "missing.html"
    target.symlink_to(missing)

    with pytest.raises(dashboard.DashboardError):
        dashboard.write_dashboard(target, "new", overwrite=False)

    assert target.is_symlink()
    assert not missing.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_writer_no_overwrite_preserves_a_concurrently_created_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "report.html"
    original_temporary = dashboard.tempfile.NamedTemporaryFile

    def concurrent_creator(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        temporary = original_temporary(*args, **kwargs)
        target.write_bytes(b"concurrent report")
        return temporary

    monkeypatch.setattr(dashboard.tempfile, "NamedTemporaryFile", concurrent_creator)
    with pytest.raises(dashboard.DashboardError):
        dashboard.write_dashboard(target, "new", overwrite=False)

    assert target.read_bytes() == b"concurrent report"
    assert not list(tmp_path.glob("*.tmp"))
