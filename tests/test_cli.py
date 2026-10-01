from __future__ import annotations

import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from glassbox.events import DecisionEvent, EvidenceEvent, SpanEvent, TraceEvent
from glassbox.store import Database, Repository

TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
SPAN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAX"
TIMESTAMP = datetime(2026, 8, 22, 14, 30, 45, tzinfo=UTC)


def test_drift_cli_detected_status_is_json_and_exit_zero(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from glassbox.eval.drift import create_baseline, load_policy
    from tests.eval.test_drift import AS_OF, _seed_drift

    path = tmp_path / "drift.sqlite3"
    _seed_drift(path, recent_at=datetime.now(UTC).replace(microsecond=0) - timedelta(days=2))
    policy = load_policy(
        Path(__file__).resolve().parent.parent / "glassbox/eval/policies/drift_v1.toml"
    )
    create_baseline(path, "agent-a", policy, supersede=False, clock=lambda: AS_OF)
    assert main(["--database", str(path), "drift", "--agent", "agent-a"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "drift_detected"
    assert payload["policy_hash"] == policy.policy_hash


def test_drift_cli_baseline_and_operational_exits(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_drift import _seed_drift

    missing = tmp_path / "missing.sqlite3"
    assert main(["--database", str(missing), "drift", "--agent", "agent-a"]) == 2
    assert "unable to run drift command" in capsys.readouterr().err
    path = tmp_path / "drift.sqlite3"
    _seed_drift(path, recent_at=datetime.now(UTC).replace(microsecond=0) - timedelta(days=2))
    baseline_args = ["--database", str(path), "drift", "baseline", "--agent", "agent-a"]
    assert main(baseline_args) == 0
    assert main(baseline_args) == 2
    assert main(baseline_args + ["--supersede-baseline"]) == 0
    assert "unable to run drift command" in capsys.readouterr().err


def test_drift_cli_insufficient_baseline_exits_two(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_drift import _seed_drift

    path = tmp_path / "drift.sqlite3"
    _seed_drift(path, baseline_decisions=99)
    assert main(["--database", str(path), "drift", "baseline", "--agent", "agent-a"]) == 2
    assert capsys.readouterr().err.strip() == "glassbox: unable to run drift command"


def test_drift_cli_malformed_baseline_exits_two(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_drift import _seed_drift

    path = tmp_path / "drift.sqlite3"
    _seed_drift(path)
    assert main(["--database", str(path), "drift", "baseline", "--agent", "agent-a"]) == 0
    capsys.readouterr()
    database = Database.open(path)
    try:
        database.connection.execute("UPDATE drift_baselines SET reference_json = '{}' ")
        database.connection.commit()
    finally:
        database.close()
    assert main(["--database", str(path), "drift", "--agent", "agent-a"]) == 2
    assert capsys.readouterr().err.strip() == "glassbox: unable to run drift command"


def test_drift_cli_policy_override_and_insufficient_baseline(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_drift import POLICY_PATH, _seed_drift

    path = tmp_path / "drift.sqlite3"
    _seed_drift(path, recent_at=datetime.now(UTC).replace(microsecond=0) - timedelta(days=2))
    assert main(["--database", str(path), "drift", "--agent", "agent-a"]) == 0
    assert json.loads(capsys.readouterr().out)["reason"] == "baseline_not_created"
    custom = tmp_path / "custom.toml"
    custom.write_text(
        POLICY_PATH.read_text().replace("warning_threshold = 0.10", "warning_threshold = 0.11")
    )
    args = [
        "--database",
        str(path),
        "drift",
        "baseline",
        "--agent",
        "agent-a",
        "--policy",
        str(custom),
    ]
    assert main(args) == 0
    capsys.readouterr()
    assert main(["--database", str(path), "drift", "--agent", "agent-a"]) == 0
    assert json.loads(capsys.readouterr().out)["reason"] == "policy_changed_requires_rebaseline"
    assert (
        main(["--database", str(path), "drift", "--agent", "agent-a", "--policy", str(custom)]) == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "drift_detected"


def _repository_with_trace(path: Path) -> Database:
    database = Database.open(path)
    repository = Repository(database)
    repository.write_event(
        TraceEvent(
            trace_id=TRACE_ID,
            agent_name="replenishment-triage",
            agent_version="test",
            started_at=TIMESTAMP,
            ended_at=TIMESTAMP,
            environment="dev",
        )
    )
    repository.write_event(
        SpanEvent(
            span_id=SPAN_ID,
            trace_id=TRACE_ID,
            name="triage.run",
            span_kind="compute",
            started_at=TIMESTAMP,
            ended_at=TIMESTAMP,
        )
    )
    repository.write_event(
        DecisionEvent(
            decision_id=DECISION_ID,
            trace_id=TRACE_ID,
            agent_name="replenishment-triage",
            agent_version="test",
            entity_type="exception",
            entity_id="EXC-REDACTED",
            decision_type="triage",
            recommendation={"action": "review"},
            rationale="A redacted test decision.",
            rationale_citations=("inventory",),
            confidence=0.8,
            alternatives_considered=(),
            decided_at=TIMESTAMP,
        )
    )
    repository.write_event(
        EvidenceEvent(
            evidence_id="inventory",
            decision_id=DECISION_ID,
            source_system="sample",
            source_ref="redacted",
            field_name="on_hand",
            field_value=0,
            weight=0.8,
            retrieved_at=TIMESTAMP,
        )
    )
    return database


def test_trace_command_emits_a_stable_trace_tree_json(tmp_path: Path, capsys) -> None:
    from glassbox.cli import main

    database_path = tmp_path / "glassbox.sqlite3"
    database = _repository_with_trace(database_path)
    database.close()

    assert main(["--database", str(database_path), "trace", TRACE_ID]) == 0

    first_output = capsys.readouterr().out
    assert main(["--database", str(database_path), "trace", TRACE_ID]) == 0
    second_output = capsys.readouterr().out

    assert first_output == second_output
    assert json.loads(first_output) == {
        "decisions": [
            {
                "decision": {
                    "agent_name": "replenishment-triage",
                    "agent_version": "test",
                    "alternatives_considered": [],
                    "confidence": 0.8,
                    "decided_at": "2026-08-22T14:30:45Z",
                    "decision_id": DECISION_ID,
                    "decision_type": "triage",
                    "entity_id": "EXC-REDACTED",
                    "entity_type": "exception",
                    "rationale": "A redacted test decision.",
                    "rationale_citations": ["inventory"],
                    "recommendation": {"action": "review"},
                    "trace_id": TRACE_ID,
                },
                "evidence": [
                    {
                        "decision_id": DECISION_ID,
                        "evidence_id": "inventory",
                        "field_name": "on_hand",
                        "field_value": 0,
                        "retrieved_at": "2026-08-22T14:30:45Z",
                        "source_ref": "redacted",
                        "source_system": "sample",
                        "weight": 0.8,
                    }
                ],
            }
        ],
        "spans": [
            {
                "ended_at": "2026-08-22T14:30:45Z",
                "attributes": {},
                "name": "triage.run",
                "span_id": SPAN_ID,
                "span_kind": "compute",
                "started_at": "2026-08-22T14:30:45Z",
                "trace_id": TRACE_ID,
            }
        ],
        "trace": {
            "agent_name": "replenishment-triage",
            "agent_version": "test",
            "attributes": {},
            "ended_at": "2026-08-22T14:30:45Z",
            "environment": "dev",
            "started_at": "2026-08-22T14:30:45Z",
            "status": "ok",
            "trace_id": TRACE_ID,
        },
    }


def test_trace_command_reads_committed_data_while_the_writer_connection_is_open(
    tmp_path: Path, capsys
) -> None:
    """A trace is fully committed as soon as write_event() returns, but in WAL
    mode that commit can still be sitting only in the -wal file until the last
    connection closes or an auto-checkpoint fires. The CLI must see it anyway --
    that's the normal case of inspecting a trace from a still-running agent."""
    from glassbox.cli import main

    database_path = tmp_path / "glassbox.sqlite3"
    database = _repository_with_trace(database_path)
    try:
        assert main(["--database", str(database_path), "trace", TRACE_ID]) == 0
    finally:
        database.close()

    output = capsys.readouterr().out
    assert json.loads(output)["trace"]["trace_id"] == TRACE_ID


def test_trace_command_never_mutates_the_database(tmp_path: Path, capsys) -> None:
    """Reading a WAL-mode database inherently needs a wal-index for any reader,
    creating -wal/-shm sidecar files even for a read-only connection -- that is
    expected and is what makes reading live, uncheckpointed data possible. What
    must never happen is a mutation of the stored records."""
    import sqlite3

    from glassbox.cli import main

    database_path = tmp_path / "glassbox.sqlite3"
    database = _repository_with_trace(database_path)
    database.close()
    inspection = sqlite3.connect(database_path)
    rows_before = inspection.execute("SELECT * FROM traces").fetchall()
    inspection.close()

    assert main(["--database", str(database_path), "trace", TRACE_ID]) == 0
    capsys.readouterr()

    inspection = sqlite3.connect(database_path)
    rows_after = inspection.execute("SELECT * FROM traces").fetchall()
    inspection.close()
    assert rows_after == rows_before


def test_export_command_writes_one_read_only_card(tmp_path: Path, capsys) -> None:
    from glassbox.cli import main

    database_path = tmp_path / "glassbox.sqlite3"
    database = _repository_with_trace(database_path)
    database.close()
    target = tmp_path / "card.html"
    arguments = [
        "--database",
        str(database_path),
        "export",
        "--decision",
        DECISION_ID,
        "--output",
        str(target),
    ]

    assert main(arguments) == 0
    capsys.readouterr()

    html = target.read_text(encoding="utf-8")
    assert "Decision brief" in html
    assert "Feedback is read-only" in html
    assert "<form" not in html


def test_export_command_rejects_missing_decision_and_accidental_overwrite(
    tmp_path: Path, capsys
) -> None:
    from glassbox.cli import main

    database_path = tmp_path / "glassbox.sqlite3"
    database = _repository_with_trace(database_path)
    database.close()
    target = tmp_path / "card.html"
    arguments = [
        "--database",
        str(database_path),
        "export",
        "--decision",
        DECISION_ID,
        "--output",
        str(target),
    ]

    assert main(arguments[:4] + ["01ARZ3NDEKTSV4RRFFQ69G5FBK"] + arguments[5:]) == 1
    assert main(arguments) == 0
    assert main(arguments) == 2
    assert "unable to export decision" in capsys.readouterr().err


def test_eval_command_prints_result_and_returns_gate_status(tmp_path: Path, capsys) -> None:
    import yaml

    from glassbox.cli import main

    (tmp_path / "schema.json").write_text('{"type":"object","required":["urgency","action"]}')
    (tmp_path / "case.yaml").write_text(
        yaml.safe_dump({"case_id": "case-001", "input": {}, "expected_labels": {"urgency": "HIGH"}})
    )
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "target": "tests.eval.runner_target:run_case",
                "schema": "schema.json",
                "cases": ["case.yaml"],
                "gates": {"deterministic_pass_rate": 1.0},
            }
        )
    )

    assert main(["eval", "--suite", str(manifest)]) == 0
    assert json.loads(capsys.readouterr().out)["gates"]["passed"] is True


# --- `judge` command -------------------------------------------------------

_LOOPBACK_CONFIG_ARGS = dict(
    provider="ollama",
    model="llama3",
    credential=None,
    base_url="http://127.0.0.1:11434",
    is_remote=False,
)

_REMOTE_CONFIG_ARGS = dict(
    provider="openai",
    model="gpt-4o",
    credential="test-secret",
    base_url="https://api.openai.com/v1",
    is_remote=True,
)


@dataclass(frozen=True)
class _FakeJudgeReport:
    """A minimal stand-in for `JudgeReport`: only `status`/`to_dict()` are used by the CLI."""

    status: str

    def to_dict(self) -> dict[str, object]:
        return {"status": self.status, "reason": "fake"}


def _patch_judge_config(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
    from glassbox.eval.judge_config import JudgeConfig

    values = dict(_LOOPBACK_CONFIG_ARGS)
    values.update(overrides)
    config = JudgeConfig(**values)  # type: ignore[arg-type]
    monkeypatch.setattr(
        "glassbox.eval.judge_config.load_judge_config",
        lambda project_root, environ: config,
    )


def _patch_judge_config_error(monkeypatch: pytest.MonkeyPatch, message: str) -> None:
    from glassbox.eval.judge_config import JudgeConfigError

    def _raise(project_root: Path, environ: object) -> None:
        raise JudgeConfigError(message)

    monkeypatch.setattr("glassbox.eval.judge_config.load_judge_config", _raise)


def _patch_run_judge(
    monkeypatch: pytest.MonkeyPatch, report: _FakeJudgeReport
) -> list[tuple[object, ...]]:
    calls: list[tuple[object, ...]] = []

    def fake_run_judge(database_path, config, decided_since, max_cases, allow_self_judge):  # type: ignore[no-untyped-def]
        calls.append((database_path, config, decided_since, max_cases, allow_self_judge))
        return report

    monkeypatch.setattr("glassbox.eval.judge.run_judge", fake_run_judge)
    return calls


def _patch_run_judge_raising(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> None:
    def fake_run_judge(database_path, config, decided_since, max_cases, allow_self_judge):  # type: ignore[no-untyped-def]
        raise exc

    monkeypatch.setattr("glassbox.eval.judge.run_judge", fake_run_judge)


def test_serve_export_and_eval_never_import_the_judge_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    """`glassbox.eval.judge` pulls in the optional `dotenv` dependency and the
    judge/provider/egress stack; `serve`, `export`, and `eval` must never
    trigger loading it. Setting the sys.modules entry to `None` makes any
    attempted `import glassbox.eval.judge` raise `ImportError` immediately,
    so this test fails loudly if a future change adds such an import."""
    monkeypatch.setitem(sys.modules, "glassbox.eval.judge", None)

    from glassbox.cli import main

    database_path = tmp_path / "glassbox.sqlite3"
    database = _repository_with_trace(database_path)
    database.close()

    assert main(["serve", "--host", "127.0.0.1", "--port", "8787"]) == 2
    capsys.readouterr()

    output_path = tmp_path / "card.html"
    assert (
        main(
            [
                "--database",
                str(database_path),
                "export",
                "--decision",
                DECISION_ID,
                "--output",
                str(output_path),
            ]
        )
        == 0
    )
    capsys.readouterr()

    import yaml

    (tmp_path / "schema.json").write_text('{"type":"object","required":["urgency","action"]}')
    (tmp_path / "case.yaml").write_text(
        yaml.safe_dump({"case_id": "case-001", "input": {}, "expected_labels": {"urgency": "HIGH"}})
    )
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "target": "tests.eval.runner_target:run_case",
                "schema": "schema.json",
                "cases": ["case.yaml"],
                "gates": {"deterministic_pass_rate": 1.0},
            }
        )
    )
    assert main(["eval", "--suite", str(manifest)]) == 0


def test_judge_command_returns_zero_for_an_uncalibrated_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    from glassbox.cli import main

    monkeypatch.chdir(tmp_path)
    Database.open(tmp_path / "glassbox.sqlite3").close()
    _patch_judge_config(monkeypatch)
    calls = _patch_run_judge(monkeypatch, _FakeJudgeReport(status="uncalibrated"))

    assert main(["judge", "--since", "7d"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "uncalibrated"
    assert len(calls) == 1


def test_judge_command_returns_one_for_uncalibrated_when_calibration_is_required(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    from glassbox.cli import main

    monkeypatch.chdir(tmp_path)
    Database.open(tmp_path / "glassbox.sqlite3").close()
    _patch_judge_config(monkeypatch)
    _patch_run_judge(monkeypatch, _FakeJudgeReport(status="uncalibrated"))

    assert main(["judge", "--since", "7d", "--require-calibrated"]) == 1
    capsys.readouterr()


def test_judge_command_returns_one_for_a_calibrated_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    from glassbox.cli import main

    monkeypatch.chdir(tmp_path)
    Database.open(tmp_path / "glassbox.sqlite3").close()
    _patch_judge_config(monkeypatch)
    _patch_run_judge(monkeypatch, _FakeJudgeReport(status="failed"))

    assert main(["judge", "--since", "7d"]) == 1

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "failed"


def test_judge_command_returns_two_on_a_preflight_configuration_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    from glassbox.cli import main

    monkeypatch.chdir(tmp_path)
    Database.open(tmp_path / "glassbox.sqlite3").close()
    _patch_judge_config_error(monkeypatch, "GLASSBOX_JUDGE_PROVIDER is required")

    assert main(["judge", "--since", "7d"]) == 2

    error = capsys.readouterr().err
    assert "GLASSBOX_JUDGE_PROVIDER is required" in error


def test_judge_command_passed_status_and_a_calibrated_run_return_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    from glassbox.cli import main

    monkeypatch.chdir(tmp_path)
    Database.open(tmp_path / "glassbox.sqlite3").close()
    _patch_judge_config(monkeypatch)
    _patch_run_judge(monkeypatch, _FakeJudgeReport(status="passed"))

    assert main(["judge", "--since", "7d", "--require-calibrated"]) == 0
    capsys.readouterr()


def test_judge_command_rejects_a_remote_provider_without_confirm_egress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    from glassbox.cli import main

    monkeypatch.chdir(tmp_path)
    Database.open(tmp_path / "glassbox.sqlite3").close()
    _patch_judge_config(monkeypatch, **_REMOTE_CONFIG_ARGS)
    calls = _patch_run_judge(monkeypatch, _FakeJudgeReport(status="passed"))

    assert main(["judge", "--since", "7d"]) == 2

    assert calls == []
    error = capsys.readouterr().err
    assert "--confirm-egress" in error


def test_judge_command_allows_a_remote_provider_with_confirm_egress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    from glassbox.cli import main

    monkeypatch.chdir(tmp_path)
    Database.open(tmp_path / "glassbox.sqlite3").close()
    _patch_judge_config(monkeypatch, **_REMOTE_CONFIG_ARGS)
    calls = _patch_run_judge(monkeypatch, _FakeJudgeReport(status="passed"))

    assert main(["judge", "--since", "7d", "--confirm-egress"]) == 0

    assert len(calls) == 1
    capsys.readouterr()


def test_judge_command_prints_the_preflight_disclosure_for_a_loopback_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    """Loopback Ollama never requires `--confirm-egress`, but the same
    disclosure is still printed before `run_judge()` runs."""
    from glassbox.cli import main

    monkeypatch.chdir(tmp_path)
    Database.open(tmp_path / "glassbox.sqlite3").close()
    _patch_judge_config(monkeypatch)
    _patch_run_judge(monkeypatch, _FakeJudgeReport(status="uncalibrated"))

    assert main(["judge", "--since", "7d"]) == 0

    error = capsys.readouterr().err
    assert "provider=ollama" in error
    assert "model=llama3" in error
    assert "base_url=http://127.0.0.1:11434" in error
    assert "calibration_backlog_count=0" in error
    assert "recent_gated_count=0" in error
    assert "deduplicated_total=0" in error
    assert "send_count=0" in error
    assert "self_judge_unverified_count=0" in error


def test_judge_command_returns_two_for_a_nonexistent_database_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    """A mistyped --database path is exactly the class of preflight mistake this
    command's exit-code contract must catch: it must fail loudly with exit 2,
    never silently fabricate an empty database and report a hollow 0-candidate
    'uncalibrated' success."""
    from glassbox.cli import main

    monkeypatch.chdir(tmp_path)
    _patch_judge_config(monkeypatch)
    calls = _patch_run_judge(monkeypatch, _FakeJudgeReport(status="passed"))

    missing_path = tmp_path / "typo.sqlite3"

    assert main(["--database", str(missing_path), "judge", "--since", "7d"]) == 2

    assert calls == []
    assert not missing_path.exists()
    error = capsys.readouterr().err
    assert "unable to read judge candidates" in error


def test_judge_command_closes_the_preflight_connection_even_when_the_query_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    """Regression test for the preflight connection leak fixed alongside this
    task: whatever happens during the preflight judge_candidates() read, the
    read-only connection opened for it must be closed before returning."""
    from glassbox.cli import main

    monkeypatch.chdir(tmp_path)
    Database.open(tmp_path / "glassbox.sqlite3").close()
    _patch_judge_config(monkeypatch)
    calls = _patch_run_judge(monkeypatch, _FakeJudgeReport(status="passed"))

    close_calls: list[object] = []
    original_close = Database.close

    def tracking_close(self: Database) -> None:
        close_calls.append(self)
        original_close(self)

    monkeypatch.setattr(Database, "close", tracking_close)

    def raising_judge_candidates(self: Repository, cohort: object, decided_since: object) -> None:
        raise sqlite3.Error("simulated mid-query failure")

    monkeypatch.setattr(Repository, "judge_candidates", raising_judge_candidates)

    assert main(["judge", "--since", "7d"]) == 2

    assert len(close_calls) == 1
    assert calls == []
    error = capsys.readouterr().err
    assert "unable to read judge candidates" in error


def test_judge_command_rejects_an_invalid_duration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    from glassbox.cli import main

    monkeypatch.chdir(tmp_path)

    for invalid in ("7", "7x", "0d", "-1d", "1.5h", "d7"):
        with pytest.raises(SystemExit) as excinfo:
            main(["judge", "--since", invalid])
        assert excinfo.value.code == 2
        capsys.readouterr()


def test_judge_command_rejects_zero_or_negative_max_cases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    from glassbox.cli import main

    monkeypatch.chdir(tmp_path)

    for invalid in ("0", "-1", "not-a-number"):
        with pytest.raises(SystemExit) as excinfo:
            main(["judge", "--since", "7d", "--max-cases", invalid])
        assert excinfo.value.code == 2
        capsys.readouterr()


def test_judge_command_passes_parsed_arguments_through_to_run_judge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    from glassbox.cli import main

    monkeypatch.chdir(tmp_path)
    Database.open(tmp_path / "glassbox.sqlite3").close()
    _patch_judge_config(monkeypatch)
    calls = _patch_run_judge(monkeypatch, _FakeJudgeReport(status="uncalibrated"))

    before = datetime.now(UTC)
    assert (
        main(
            [
                "judge",
                "--since",
                "30m",
                "--max-cases",
                "5",
                "--allow-self-judge",
            ]
        )
        == 0
    )
    after = datetime.now(UTC)
    capsys.readouterr()

    [(database_path, config, decided_since, max_cases, allow_self_judge)] = calls
    assert database_path == Path("glassbox.sqlite3")
    # `run_judge` now receives a precomputed cutoff (Finding 4), not the raw
    # `--since` duration -- the CLI resolves it once against "now" itself.
    assert isinstance(decided_since, datetime)
    assert before - timedelta(minutes=30) <= decided_since <= after - timedelta(minutes=30)
    assert max_cases == 5
    assert allow_self_judge is True


def test_judge_command_uses_an_identical_decided_since_for_disclosure_and_run_judge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    """Regression test for Finding 4: the CLI's preflight disclosure query and
    the actual run_judge() call must use the exact same `decided_since` cutoff,
    not two independently computed values that could drift apart if a
    decision is written between the two calls."""
    from glassbox.cli import main

    monkeypatch.chdir(tmp_path)
    Database.open(tmp_path / "glassbox.sqlite3").close()
    _patch_judge_config(monkeypatch)

    preflight_decided_since: list[object] = []
    original_judge_candidates = Repository.judge_candidates

    def spy_judge_candidates(self, cohort, decided_since):  # type: ignore[no-untyped-def]
        preflight_decided_since.append(decided_since)
        return original_judge_candidates(self, cohort, decided_since)

    monkeypatch.setattr(Repository, "judge_candidates", spy_judge_candidates)
    calls = _patch_run_judge(monkeypatch, _FakeJudgeReport(status="uncalibrated"))

    assert main(["judge", "--since", "7d"]) == 0
    capsys.readouterr()

    assert len(preflight_decided_since) == 1
    [(_, _, run_judge_decided_since, _, _)] = calls
    assert run_judge_decided_since == preflight_decided_since[0]


def test_judge_command_returns_two_when_run_judge_raises_a_missing_dependency_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    """Regression test for Finding 1: an uncaught exception escaping
    run_judge() (a missing optional SDK extra, a provider-SDK constructor
    error, a SQLite error during persistence, ...) must exit 2 -- the
    operational-error code used elsewhere in this command -- never exit 1,
    which Python's own default for an uncaught exception collides with and
    which this command's documented exit-code contract reserves for an
    eligible calibrated-gate failure."""
    from glassbox.cli import main

    monkeypatch.chdir(tmp_path)
    Database.open(tmp_path / "glassbox.sqlite3").close()
    _patch_judge_config(monkeypatch)
    _patch_run_judge_raising(monkeypatch, ModuleNotFoundError("No module named 'anthropic'"))

    assert main(["judge", "--since", "7d"]) == 2

    error = capsys.readouterr().err
    assert "judge run failed" in error
    assert "anthropic" in error


def test_judge_command_returns_two_when_run_judge_raises_a_generic_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    from glassbox.cli import main

    monkeypatch.chdir(tmp_path)
    Database.open(tmp_path / "glassbox.sqlite3").close()
    _patch_judge_config(monkeypatch)
    _patch_run_judge_raising(
        monkeypatch, RuntimeError("simulated sqlite error while persisting the run")
    )

    assert main(["judge", "--since", "7d"]) == 2

    error = capsys.readouterr().err
    assert "judge run failed" in error
    assert "simulated sqlite error" in error


_P2_STORE_ROOT = Path(__file__).parents[1] / "glassbox" / "store"
_P2_JUDGE_TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FCA"
_P2_JUDGE_DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FCB"
_P2_JUDGE_TIMESTAMP = "2026-09-07T12:00:00Z"


def _create_released_p2_database(path: Path) -> None:
    """Write a database at the released P2 (strict schema + `feedback` table)
    fingerprint, before the P3 judge-calibration columns existed -- mirrors
    the fixture in ``tests/store/test_database.py``'s
    ``_create_released_p2_database`` (Task 1), duplicated here rather than
    imported since the ``tests`` tree has no ``__init__.py`` packages to
    import across reliably."""
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            (_P2_STORE_ROOT / "migrations" / "001_initial.sql").read_text(encoding="utf-8")
        )
        connection.executescript(
            (_P2_STORE_ROOT / "migrations" / "002_feedback.sql").read_text(encoding="utf-8")
        )
        connection.execute(
            """
            INSERT INTO traces (
                trace_id, agent_name, agent_version, started_at, status, environment
            )
            VALUES (?, 'agent', 'version', ?, 'ok', 'dev')
            """,
            (_P2_JUDGE_TRACE_ID, _P2_JUDGE_TIMESTAMP),
        )
        connection.execute(
            """
            INSERT INTO decisions (
                decision_id, trace_id, agent_name, agent_version, entity_type, entity_id,
                decision_type, recommendation, rationale, rationale_citations, confidence,
                alternatives_considered, decided_at
            ) VALUES (?, ?, 'agent', 'version', 'sku_dc', 'sku-1', 'flag_exception',
                      '{}', 'reason', '[]', 0.5, '[]', ?)
            """,
            (_P2_JUDGE_DECISION_ID, _P2_JUDGE_TRACE_ID, _P2_JUDGE_TIMESTAMP),
        )
        connection.commit()
    finally:
        connection.close()


def test_judge_command_preflight_upgrades_a_legitimate_p2_database_to_p3(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    """Regression test for Finding 2: commit ffb22ea switched the preflight
    read to Database.open_read_only(), which only accepts the database's
    exact *current* schema fingerprint and has no upgrade logic. Every user
    with a not-yet-upgraded P2 (or older) database got a misleading
    "unsupported schema" error on their very first `glassbox judge`
    invocation. The preflight read must use Database.open() (write-mode,
    with upgrade logic) once the path is confirmed to exist, exactly like
    every other Glassbox command -- so a legitimate P2 database still
    upgrades correctly, and the missing/typo'd-path case (the bug ffb22ea
    fixed) still fails fast without creating anything."""
    from glassbox.cli import main

    monkeypatch.chdir(tmp_path)
    database_path = tmp_path / "glassbox.sqlite3"
    _create_released_p2_database(database_path)
    _patch_judge_config(monkeypatch)
    calls = _patch_run_judge(monkeypatch, _FakeJudgeReport(status="uncalibrated"))

    exit_code = main(["--database", str(database_path), "judge", "--since", "7d"])
    error = capsys.readouterr().err

    assert exit_code == 0, error
    assert "unsupported schema" not in error
    assert len(calls) == 1

    # open_read_only() only accepts the exact current (P3) schema
    # fingerprint, so a successful open here proves the upgrade actually
    # happened during the preflight read.
    upgraded = Database.open_read_only(database_path)
    upgraded.close()


def test_judge_command_preflight_discloses_the_self_judge_unverified_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    """Regression test for Finding 3: a candidate decision with no recorded
    LLM span model must be visible in the CLI's preflight disclosure --
    before egress -- not only in the final JudgeReport after a run has
    already happened."""
    from glassbox.cli import main

    monkeypatch.chdir(tmp_path)
    database_path = tmp_path / "glassbox.sqlite3"
    database = Database.open(database_path)
    repository = Repository(database)
    decided_at = datetime.now(UTC) - timedelta(minutes=5)
    trace_id = "01ARZ3NDEKTSV4RRFFQ69G5FDA"
    decision_id = "01ARZ3NDEKTSV4RRFFQ69G5FDB"
    repository.write_event(
        TraceEvent(
            trace_id=trace_id,
            agent_name="replenishment-triage",
            agent_version="test",
            started_at=decided_at,
            ended_at=decided_at,
            environment="dev",
        )
    )
    repository.write_event(
        DecisionEvent(
            decision_id=decision_id,
            trace_id=trace_id,
            agent_name="replenishment-triage",
            agent_version="test",
            entity_type="exception",
            entity_id="EXC-1",
            decision_type="triage",
            recommendation={"action": "review"},
            rationale="No LLM span recorded for this decision.",
            rationale_citations=(),
            confidence=0.8,
            alternatives_considered=(),
            decided_at=decided_at,
        )
    )
    database.close()

    _patch_judge_config(monkeypatch)
    _patch_run_judge(monkeypatch, _FakeJudgeReport(status="uncalibrated"))

    assert main(["--database", str(database_path), "judge", "--since", "1d"]) == 0

    error = capsys.readouterr().err
    assert "recent_gated_count=1" in error
    assert "self_judge_unverified_count=1" in error
