from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from glassbox.events import DecisionEvent, EvidenceEvent, SpanEvent, TraceEvent
from glassbox.store import Database, Repository

TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
SPAN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAX"
TIMESTAMP = datetime(2026, 8, 22, 14, 30, 45, tzinfo=UTC)


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

    def fake_run_judge(database_path, config, since, max_cases, allow_self_judge):  # type: ignore[no-untyped-def]
        calls.append((database_path, config, since, max_cases, allow_self_judge))
        return report

    monkeypatch.setattr("glassbox.eval.judge.run_judge", fake_run_judge)
    return calls


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
    assert "deduplicated_send_count=0" in error


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
    capsys.readouterr()

    from datetime import timedelta

    [(database_path, config, since, max_cases, allow_self_judge)] = calls
    assert database_path == Path("glassbox.sqlite3")
    assert since == timedelta(minutes=30)
    assert max_cases == 5
    assert allow_self_judge is True
