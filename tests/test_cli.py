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
LEGACY_DRIFT_POLICY_ARGS = [
    "--policy", str(Path(__file__).resolve().parents[1] / "glassbox/eval/policies/drift_v1.toml")
]
DRIFT_WINDOW_ARGS = ["--start", "2026-01-01T00:00:00Z", "--end", "2026-06-01T00:00:00Z"]


@pytest.mark.parametrize("position", ["parent", "baseline"])
def test_drift_baseline_policy_position_preserves_selected_policy(
    tmp_path: Path, capsys, position: str
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from glassbox.eval.drift import load_policy
    from tests.eval.test_drift import LATENCY_POLICY_TEXT, _seed_drift

    path = tmp_path / "drift.sqlite3"
    _seed_drift(path)
    policy_path = tmp_path / "latency.toml"
    policy_path.write_text(LATENCY_POLICY_TEXT.replace(
        "minimum_baseline_samples = 200", "minimum_baseline_samples = 50"
    ))
    before = ["--policy", str(policy_path)] if position == "parent" else []
    after = ["--policy", str(policy_path)] if position == "baseline" else []
    assert main([
        "--database", str(path), "drift", *before, "baseline", "--agent", "agent-a",
        *after, "--start", "2026-01-01T00:00:00Z", "--end", "2026-06-01T00:00:00Z",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["policy_hash"] == load_policy(policy_path).policy_hash
    assert payload["baseline_window"]["start"] == "2026-01-01T00:00:00+00:00"


def _counterfactual_cli_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *,
                              remote: bool, responses: list[str | Exception],
                              count: int = 1):
    from glassbox.eval.counterfactual_config import CounterfactualConfig
    from tests.eval.test_counterfactual import FakeProvider, _seed

    database_path = tmp_path / "counterfactual.sqlite3"
    _seed(database_path, count=count)
    config = CounterfactualConfig(
        "openai" if remote else "ollama", "variant-model",
        "PRIVATE_CREDENTIAL" if remote else None,
        "https://api.openai.com/v1" if remote else "http://127.0.0.1:11434", remote,
    )
    provider = FakeProvider(responses)
    factories: list[object] = []
    monkeypatch.setattr(
        "glassbox.eval.counterfactual_config.load_counterfactual_config",
        lambda *_: config,
    )

    def factory(selected_config):  # type: ignore[no-untyped-def]
        factories.append(selected_config)
        return provider

    monkeypatch.setattr("glassbox.eval.counterfactual_provider.create_counterfactual_provider",
                        factory)
    return database_path, provider, factories


def _counterfactual_run_args(database_path: Path) -> list[str]:
    return [
        "--database", str(database_path), "counterfactual", "run",
        "--prompt", str(Path(__file__).parents[1] / "glassbox/eval/prompts/counterfactual_v1.md"),
        "--since", "90d",
    ]


def test_counterfactual_remote_preflight_requires_confirmation_before_provider_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_counterfactual import _answer

    path, provider, factories = _counterfactual_cli_setup(
        tmp_path, monkeypatch, remote=True, responses=[_answer()],
    )
    assert main(_counterfactual_run_args(path)) == 2
    captured = capsys.readouterr()
    assert "provider=openai" in captured.err
    assert "model=variant-model" in captured.err
    assert "base_url=https://api.openai.com/v1" in captured.err
    assert "prompt_version=counterfactual_v1" in captured.err
    assert "selected_count=1 capture_count=0" in captured.err
    assert captured.err.endswith("glassbox: unable to run counterfactual command\n")
    assert captured.out == ""
    assert factories == []
    assert provider.calls == []


def test_counterfactual_config_reads_glassbox_root_not_caller_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from glassbox.eval import counterfactual_config
    from tests.eval.test_counterfactual import _seed

    database_path = tmp_path / "cases.sqlite3"
    _seed(database_path)
    sibling = tmp_path / "sibling-agent"
    sibling.mkdir()
    (sibling / ".env").write_text(
        "GLASSBOX_COUNTERFACTUAL_PROVIDER=ollama\n"
        "GLASSBOX_COUNTERFACTUAL_MODEL=from-sibling\n"
    )
    monkeypatch.chdir(sibling)
    for key in (
        "GLASSBOX_COUNTERFACTUAL_PROVIDER", "GLASSBOX_COUNTERFACTUAL_MODEL",
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    dotenv_paths: list[Path] = []

    def capture_dotenv(path: Path, **kwargs):  # type: ignore[no-untyped-def]
        dotenv_paths.append(path)
        return {}

    monkeypatch.setattr(counterfactual_config, "dotenv_values", capture_dotenv)
    assert main(_counterfactual_run_args(database_path)) == 2
    assert dotenv_paths == [Path(__file__).resolve().parents[1] / ".env"]
    assert capsys.readouterr().out == ""


def test_counterfactual_loopback_ollama_discloses_without_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_counterfactual import _answer

    path, provider, factories = _counterfactual_cli_setup(
        tmp_path, monkeypatch, remote=False, responses=[_answer()],
    )
    assert main(_counterfactual_run_args(path)) == 0
    captured = capsys.readouterr()
    assert "provider=ollama" in captured.err
    assert "model=variant-model" in captured.err
    assert "base_url=http://127.0.0.1:11434" in captured.err
    assert "prompt_version=counterfactual_v1" in captured.err
    assert "prompt_hash=" in captured.err
    assert "selected_count=1" in captured.err
    assert "capture_count=0" in captured.err
    assert "PRIVATE_CREDENTIAL" not in captured.err
    assert len(factories) == len(provider.calls) == 1
    assert json.loads(captured.out)["succeeded_count"] == 1


def test_counterfactual_run_prints_compact_json_and_zero_for_partial_case_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_counterfactual import _answer

    path, _, _ = _counterfactual_cli_setup(
        tmp_path, monkeypatch, remote=True, responses=["bad JSON", _answer()], count=2,
    )
    assert main(_counterfactual_run_args(path) + ["--confirm-egress", "--max-cases", "2"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert (payload["selected_count"], payload["succeeded_count"], payload["failed_count"]) == (
        2, 1, 1,
    )
    assert captured.out == json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    assert "PRIVATE_CREDENTIAL" not in captured.err + captured.out
    assert "ORIGINAL_RATIONALE" not in captured.err


def test_counterfactual_run_returns_two_for_zero_successes_after_persisting_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main

    path, _, _ = _counterfactual_cli_setup(
        tmp_path, monkeypatch, remote=False, responses=["invalid response"],
    )
    assert main(_counterfactual_run_args(path)) == 2
    failed = json.loads(capsys.readouterr().out)
    assert failed["selected_count"] == 1
    assert failed["succeeded_count"] == 0
    assert failed["results"][0]["failure_code"] == "invalid_response"
    assert main(["--database", str(path), "counterfactual", "report", "--run",
                 failed["run_id"]]) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["run_id"] == failed["run_id"]
    assert replay["results"][0]["failure_code"] == "invalid_response"


def test_counterfactual_run_returns_two_for_empty_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main

    path, _, factories = _counterfactual_cli_setup(
        tmp_path, monkeypatch, remote=False, responses=[],
    )
    assert main(_counterfactual_run_args(path)[:-1] + ["1d"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["selected_count"] == 0
    assert payload["results"] == []
    assert factories == []


@pytest.mark.parametrize("execution_mode", ["offline", "live_shadow"])
def test_counterfactual_report_exposes_saved_execution_mode(
    tmp_path: Path, capsys, execution_mode: str,
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_counterfactual import FakeProvider, _answer, _execute, _seed

    path = tmp_path / "cases.sqlite3"
    _seed(path)
    original, detail = _execute(
        path, FakeProvider([_answer()]), execution_mode=execution_mode,
        enrich_outcomes=execution_mode == "offline",
    )
    assert detail.run.execution_mode == execution_mode
    assert main(["--database", str(path), "counterfactual", "report", "--run",
                 original.run_id]) == 0
    assert json.loads(capsys.readouterr().out)["execution_mode"] == execution_mode


def test_counterfactual_report_replays_original_maturity_status(
    tmp_path: Path, capsys,
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_counterfactual import (
        DECIDED,
        FakeProvider,
        _answer,
        _execute,
        _seed,
    )

    path = tmp_path / "cases.sqlite3"
    _seed(path, outcome=True)
    original, _ = _execute(
        path, FakeProvider([_answer()]), as_of=DECIDED + timedelta(days=2)
    )
    assert main(["--database", str(path), "counterfactual", "report", "--run",
                 original.run_id]) == 0
    assert json.loads(capsys.readouterr().out) == original.to_dict()


def test_counterfactual_report_returns_two_for_unknown_run_and_never_writes(
    tmp_path: Path, capsys,
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main

    path = tmp_path / "counterfactual.sqlite3"
    database = Database.open(path)
    database.close()
    before = path.read_bytes()
    assert main(["--database", str(path), "counterfactual", "report", "--run",
                 "01ARZ3NDEKTSV4RRFFQ69G5FAZ"]) == 2
    assert capsys.readouterr().err == "glassbox: unable to run counterfactual command\n"
    assert path.read_bytes() == before


def test_counterfactual_preflight_and_run_share_as_of_filter_before_case_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:  # type: ignore[no-untyped-def]
    import glassbox.cli as cli
    from tests.eval.test_counterfactual import DECIDED, DECISION_IDS, _answer

    path, provider, _ = _counterfactual_cli_setup(
        tmp_path, monkeypatch, remote=False, responses=[_answer()], count=2,
    )

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            assert tz == UTC
            return DECIDED + timedelta(seconds=30)

    monkeypatch.setattr(cli, "datetime", FixedDatetime)
    assert cli.main(_counterfactual_run_args(path) + ["--max-cases", "1"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert "selected_count=1" in captured.err
    assert payload["as_of"] == "2026-08-01T12:00:30Z"
    assert [item["decision_id"] for item in payload["results"]] == [DECISION_IDS[0]]
    assert len(provider.calls) == 1


@pytest.mark.parametrize("subcommand", ["run", "report"])
def test_counterfactual_import_failure_is_one_safe_error_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, subcommand: str,
) -> None:  # type: ignore[no-untyped-def]
    import builtins

    from glassbox.cli import main

    real_import = builtins.__import__

    def missing_optional(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "glassbox.eval.counterfactual":
            raise ModuleNotFoundError("secret path and credential must not be printed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_optional)
    path = tmp_path / "private.sqlite3"
    args = (_counterfactual_run_args(path) if subcommand == "run" else [
        "--database", str(path), "counterfactual", "report", "--run",
        "01ARZ3NDEKTSV4RRFFQ69G5FAZ",
    ])
    assert main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "glassbox: unable to run counterfactual command\n"


def test_counterfactual_disclosed_selection_is_frozen_against_backdated_insert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:  # type: ignore[no-untyped-def]
    import glassbox.cli as cli
    import glassbox.eval.counterfactual as counterfactual
    from tests.eval.test_counterfactual import DECIDED, DECISION_IDS, TRACE_ID, _answer

    path, provider, _ = _counterfactual_cli_setup(
        tmp_path, monkeypatch, remote=False, responses=[_answer()],
    )

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            assert tz == UTC
            return DECIDED + timedelta(seconds=30)

    monkeypatch.setattr(cli, "datetime", FixedDatetime)
    original_run = counterfactual.run_counterfactual

    def insert_then_run(*args, **kwargs):  # type: ignore[no-untyped-def]
        database = Database.open(path)
        try:
            Repository(database).write_event(DecisionEvent(
                decision_id="01ARZ3NDEKTSV4RRFFQ69G5FA9", trace_id=TRACE_ID,
                agent_name="replenishment-triage", agent_version="source-v1",
                entity_type="sku_dc", entity_id="inserted-after-disclosure",
                decision_type="triage", recommendation={"urgency": "LOW"},
                rationale="newly inserted", rationale_citations=(), confidence=0.71,
                alternatives_considered=({"action": "wait"},),
                decided_at=DECIDED + timedelta(seconds=20),
            ))
        finally:
            database.close()
        return original_run(*args, **kwargs)

    monkeypatch.setattr(counterfactual, "run_counterfactual", insert_then_run)
    assert cli.main(_counterfactual_run_args(path) + ["--max-cases", "1"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert "selected_count=1" in captured.err
    assert [result["decision_id"] for result in payload["results"]] == [DECISION_IDS[0]]
    assert len(provider.calls) == 1
    assert "inserted-after-disclosure" not in provider.calls[0][1]


@pytest.mark.parametrize("defect", ["prompt", "policy", "database"])
def test_counterfactual_bad_local_inputs_have_one_safe_error_line_before_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, defect: str,
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_counterfactual import _answer

    path, _, factories = _counterfactual_cli_setup(
        tmp_path, monkeypatch, remote=False, responses=[_answer()],
    )
    args = _counterfactual_run_args(path)
    private_path = tmp_path / "PRIVATE_PATH"
    if defect == "prompt":
        args[args.index("--prompt") + 1] = str(private_path)
    elif defect == "policy":
        args.extend(["--outcome-policy", str(private_path)])
    else:
        args[args.index("--database") + 1] = str(private_path)
    assert main(args) == 2
    captured = capsys.readouterr()
    assert captured.err == "glassbox: unable to run counterfactual command\n"
    assert captured.out == ""
    assert factories == []


@pytest.mark.parametrize("start,end", [
    ("2026-01-01T00:00:00+00:00", "2026-06-01T00:00:00Z"),
    ("2026-06-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    ("2026-01-01T00:00:00Z", "9999-06-01T00:00:00Z"),
])
def test_drift_baseline_rejects_invalid_window_before_writing(
    tmp_path: Path, capsys, start: str, end: str
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    path = tmp_path / "sensitive.sqlite3"
    database = Database.open(path)
    database.close()
    assert main(["--database", str(path), "drift", "baseline", "--agent", "agent-a",
                 "--start", start, "--end", end]) == 2
    captured = capsys.readouterr()
    assert str(path) not in captured.err
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM drift_baselines").fetchone()[0] == 0


def test_drift_baseline_warns_on_recent_overlap_and_persists_window(
    tmp_path: Path, capsys
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_drift import _seed_drift
    end = datetime.now(UTC).replace(microsecond=0) - timedelta(days=1)
    start = end - timedelta(days=2)
    path = tmp_path / "drift.sqlite3"
    _seed_drift(path, baseline_decisions=500, recent_at=start + timedelta(hours=1))
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE decisions SET decided_at = ?",
            ((start + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),),
        )
    start_text, end_text = [value.isoformat().replace("+00:00", "Z") for value in (start, end)]
    assert main(["--database", str(path), "drift", "baseline", "--agent", "agent-a",
                 "--start", start_text, "--end", end_text]) == 0
    captured = capsys.readouterr()
    assert "overlaps the recent window" in captured.err
    assert json.loads(captured.out)["baseline_window"]["end"] == end.isoformat()


def test_outcomes_import_missing_database_does_not_create_it(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_reconciliation import json_row
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GLASSBOX_DATABASE", raising=False)
    input_path = tmp_path / "input.jsonl"
    input_path.write_text(json_row())
    assert main(["outcomes", "import", "--input", str(input_path),
                 "--rejects", str(tmp_path / "rejects.jsonl")]) == 2
    assert not (tmp_path / "glassbox.sqlite3").exists()
    assert capsys.readouterr().err == "glassbox: unable to run outcomes command\n"


def test_drift_deficient_baseline_names_signal_without_database_path(
    tmp_path: Path, capsys
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_drift import LATENCY_POLICY_TEXT, _seed_drift
    path = tmp_path / "private.sqlite3"
    _seed_drift(path)
    policy = tmp_path / "latency.toml"
    policy.write_text(LATENCY_POLICY_TEXT)
    assert main(["--database", str(path), "drift", "baseline", "--agent", "agent-a",
                 "--policy", str(policy), "--start", "2026-01-01T00:00:00Z",
                 "--end", "2026-06-01T00:00:00Z"]) == 2
    captured = capsys.readouterr()
    assert "trace_latency_ms" in captured.err
    assert str(path) not in captured.err


@pytest.mark.parametrize("command", ["report", "baseline"])
def test_drift_calendar_overflow_returns_safe_operational_failure(
    tmp_path: Path, capsys, command: str
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_drift import LATENCY_POLICY_TEXT, _seed_drift

    path = tmp_path / "private.sqlite3"
    _seed_drift(path)
    policy = tmp_path / "oversized-duration.toml"
    policy.write_text(LATENCY_POLICY_TEXT.replace('"7d"', '"999999999d"'))
    operation = ["baseline"] if command == "baseline" else []
    window = DRIFT_WINDOW_ARGS if command == "baseline" else []
    assert main([
        "--database", str(path), "drift", *operation, "--agent", "agent-a",
        "--policy", str(policy), *window,
    ]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "glassbox: unable to run drift command\n"
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM drift_baselines").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM drift_runs").fetchone()[0] == 0


def test_outcomes_import_returns_one_and_writes_rejects_for_partial_success(
    tmp_path: Path, capsys
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_reconciliation import json_row, seed_outcomes_database

    path = tmp_path / "glassbox.sqlite3"
    seed_outcomes_database(path)
    input_path, rejects = tmp_path / "input.jsonl", tmp_path / "rejects.jsonl"
    input_path.write_text(json_row() + "\n" + json_row(value={"occurred": False}) + "\n")
    assert main([
        "--database", str(path), "outcomes", "import",
        "--input", str(input_path), "--rejects", str(rejects),
    ]) == 1
    captured = capsys.readouterr()
    assert captured.out == '{"accepted":1,"rejected":1,"replayed":0}\n'
    assert captured.err == ""
    assert json.loads(rejects.read_text()) == {
        "line": 2, "source_id": "stockout-1", "reason": "source_id_conflict",
    }
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM outcomes").fetchone()[0] == 1


def test_outcomes_import_returns_zero_for_clean_idempotent_replay(
    tmp_path: Path, capsys
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_reconciliation import json_row, seed_outcomes_database

    path = tmp_path / "glassbox.sqlite3"
    seed_outcomes_database(path)
    input_path, rejects = tmp_path / "input.jsonl", tmp_path / "rejects.jsonl"
    input_path.write_text(json_row() + "\n")
    args = [
        "--database", str(path), "outcomes", "import",
        "--input", str(input_path), "--rejects", str(rejects),
    ]
    assert main(args) == 0
    capsys.readouterr()
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out) == {"accepted": 0, "replayed": 1, "rejected": 0}
    assert rejects.read_bytes() == b""


@pytest.mark.parametrize("horizon", [2**63, 10**100])
def test_outcomes_import_horizon_overflow_has_defined_exit_and_safe_json(
    tmp_path: Path, capsys, horizon: int
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_reconciliation import json_row, seed_outcomes_database

    path = tmp_path / "glassbox.sqlite3"
    seed_outcomes_database(path)
    input_path, rejects = tmp_path / "input.jsonl", tmp_path / "rejects.jsonl"
    input_path.write_text(json_row(horizon_days=horizon) + "\n")
    assert main([
        "--database", str(path), "outcomes", "import",
        "--input", str(input_path), "--rejects", str(rejects),
    ]) == 1
    captured = capsys.readouterr()
    assert captured.out == '{"accepted":0,"rejected":1,"replayed":0}\n'
    assert captured.err == ""
    assert json.loads(rejects.read_text()) == {
        "line": 1, "source_id": "stockout-1", "reason": "invalid_shape",
    }
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM outcomes").fetchone()[0] == 0


def test_outcomes_import_rejects_submicrosecond_lines_and_keeps_later_valid_line(
    tmp_path: Path, capsys
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_reconciliation import json_row, seed_outcomes_database

    path = tmp_path / "glassbox.sqlite3"
    seed_outcomes_database(path)
    input_path, rejects = tmp_path / "input.jsonl", tmp_path / "rejects.jsonl"
    input_path.write_text("\n".join([
        json_row(observed_at="2026-10-04T00:00:00.1234561Z"),
        json_row(observed_at="2026-10-04T00:00:00.1234562Z"),
        json_row(observed_at="2026-10-04T00:00:00.123456Z"),
    ]) + "\n")

    assert main([
        "--database", str(path), "outcomes", "import",
        "--input", str(input_path), "--rejects", str(rejects),
    ]) == 1

    captured = capsys.readouterr()
    assert captured.out == '{"accepted":1,"rejected":2,"replayed":0}\n'
    assert captured.err == ""
    assert [json.loads(line) for line in rejects.read_text().splitlines()] == [
        {"line": number, "source_id": "stockout-1", "reason": "invalid_timestamp"}
        for number in (1, 2)
    ]
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT observed_at FROM outcomes").fetchall() == [
            ("2026-10-04T00:00:00.123456Z",)
        ]


@pytest.mark.parametrize(
    "changes",
    [
        {"source_id": "\ud800"},
        {"outcome_type": "stockout_\udfff"},
        {"value": {"occurred": True, "notes": "\ud800"}},
        {"value": {"occurred": True, "\udfff": "private"}},
        {"value": {"occurred": True, "nested": ["\ud800"]}},
    ],
)
def test_outcomes_import_rejects_lone_surrogates_and_keeps_later_valid_line(
    tmp_path: Path, capsys, changes
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_reconciliation import json_row, seed_outcomes_database

    path = tmp_path / "glassbox.sqlite3"
    seed_outcomes_database(path)
    input_path, rejects = tmp_path / "input.jsonl", tmp_path / "rejects.jsonl"
    valid_value = {"occurred": True, "notes": "\U0001f600"}
    input_path.write_text(json_row(**changes) + "\n" + json_row(value=valid_value) + "\n")

    assert main([
        "--database", str(path), "outcomes", "import",
        "--input", str(input_path), "--rejects", str(rejects),
    ]) == 1

    captured = capsys.readouterr()
    assert captured.out == '{"accepted":1,"rejected":1,"replayed":0}\n'
    assert captured.err == ""
    assert json.loads(rejects.read_text()) == {
        "line": 1, "source_id": None if "source_id" in changes else "stockout-1",
        "reason": "invalid_shape",
    }
    with sqlite3.connect(path) as connection:
        assert [json.loads(row[0]) for row in connection.execute("SELECT value FROM outcomes")] == (
            [valid_value]
        )


def test_outcomes_import_rejects_duplicate_decision_policy_before_creating_database(
    tmp_path: Path, capsys
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_reconciliation import POLICY, json_row

    policy_path = tmp_path / "ambiguous.toml"
    policy_path.write_text(POLICY + POLICY[POLICY.index("[[rules]]"):].replace(
        'outcome_type = "stockout_occurred"', 'outcome_type = "delivery_occurred"'
    ))
    path = tmp_path / "missing.sqlite3"
    input_path, rejects = tmp_path / "input.jsonl", tmp_path / "rejects.jsonl"
    input_path.write_text(json_row() + "\n")

    assert main([
        "--database", str(path), "outcomes", "import", "--policy", str(policy_path),
        "--input", str(input_path), "--rejects", str(rejects),
    ]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "glassbox: unable to run outcomes command\n"
    assert not path.exists()
    assert not rejects.exists()
    assert not rejects.with_name(".rejects.jsonl.tmp").exists()


@pytest.mark.parametrize("failure", ["policy", "input", "rejects", "database"])
def test_outcomes_commands_return_two_for_invalid_policy_or_io(
    tmp_path: Path, capsys, failure: str
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_reconciliation import json_row, seed_outcomes_database

    path = tmp_path / "glassbox.sqlite3"
    seed_outcomes_database(path)
    input_path, rejects = tmp_path / "input.jsonl", tmp_path / "rejects.jsonl"
    input_path.write_text(json_row() + "\n")
    args = ["--database", str(path), "outcomes", "import"]
    if failure == "policy":
        invalid = tmp_path / "invalid.toml"
        invalid.write_text("private-invalid-policy")
        args.extend(["--policy", str(invalid)])
    elif failure == "input":
        input_path = tmp_path / "absent.jsonl"
    elif failure == "rejects":
        rejects.mkdir()
    else:
        path.write_bytes(b"private-invalid-database")
    args.extend(["--input", str(input_path), "--rejects", str(rejects)])
    assert main(args) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "glassbox: unable to run outcomes command\n"
    if failure != "database":
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT count(*) FROM outcomes").fetchone()[0] == 0


def test_outcomes_import_looped_temporary_symlink_returns_safe_operational_exit(
    tmp_path: Path, capsys
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_reconciliation import json_row, seed_outcomes_database

    path = tmp_path / "glassbox.sqlite3"
    seed_outcomes_database(path)
    before = path.read_bytes()
    input_path, rejects = tmp_path / "input.jsonl", tmp_path / "rejects.jsonl"
    input_path.write_text(json_row() + "\n")
    rejects.write_text("old complete artifact")
    temporary = tmp_path / ".rejects.jsonl.tmp"
    temporary.symlink_to(temporary.name)

    assert main([
        "--database", str(path), "outcomes", "import",
        "--input", str(input_path), "--rejects", str(rejects),
    ]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "glassbox: unable to run outcomes command\n"
    assert path.read_bytes() == before
    assert rejects.read_text() == "old complete artifact"
    assert temporary.is_symlink()
    assert temporary.readlink() == Path(temporary.name)


def test_outcomes_report_prints_policy_provenance_and_null_metrics(
    tmp_path: Path, capsys
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from glassbox.eval.reconciliation import default_policy_path, load_policy

    path = tmp_path / "glassbox.sqlite3"
    Database.open(path).close()
    before = path.read_bytes()
    policy = load_policy(default_policy_path())
    assert main(["--database", str(path), "outcomes", "report"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["policy_version"] == policy.policy_version
    assert payload["policy_hash"] == policy.policy_hash
    assert payload["totals"]["mature"] == payload["totals"]["labelled"] == 0
    for metric in ("coverage", "precision", "recall", "override_rate"):
        assert payload["totals"][metric] is None
    assert path.read_bytes() == before


def test_outcomes_imports_stay_inside_outcomes_dispatch_branch() -> None:
    import ast

    import glassbox.cli

    tree = ast.parse(Path(glassbox.cli.__file__).read_text())
    imports = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "glassbox.eval.reconciliation"
    ]
    assert imports
    dispatch = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "arguments.command == 'outcomes'"
    )
    assert all(node in list(ast.walk(dispatch)) for node in imports)


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
    assert main([
        "--database", str(path), "drift", "--agent", "agent-a", *LEGACY_DRIFT_POLICY_ARGS
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "drift_detected"
    assert payload["policy_hash"] == policy.policy_hash
    assert payload["monitored_signals"] == [signal.name for signal in policy.signals]


def test_drift_cli_baseline_and_operational_exits(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_drift import _seed_drift

    missing = tmp_path / "missing.sqlite3"
    assert main(["--database", str(missing), "drift", "--agent", "agent-a"]) == 2
    assert "unable to run drift command" in capsys.readouterr().err
    path = tmp_path / "drift.sqlite3"
    _seed_drift(path, recent_at=datetime.now(UTC).replace(microsecond=0) - timedelta(days=2))
    baseline_args = [
        "--database", str(path), "drift", "baseline", "--agent", "agent-a",
        *LEGACY_DRIFT_POLICY_ARGS, *DRIFT_WINDOW_ARGS,
    ]
    assert main(baseline_args) == 0
    assert main(baseline_args) == 2
    assert main(baseline_args + ["--supersede-baseline"]) == 0
    assert "unable to run drift command" in capsys.readouterr().err


def test_drift_cli_insufficient_baseline_exits_two(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_drift import _seed_drift

    path = tmp_path / "drift.sqlite3"
    _seed_drift(path, baseline_decisions=99)
    assert main([
        "--database", str(path), "drift", "baseline", "--agent", "agent-a",
        *LEGACY_DRIFT_POLICY_ARGS, *DRIFT_WINDOW_ARGS,
    ]) == 2
    assert capsys.readouterr().err.strip() == (
        "glassbox: confidence baseline has 99 samples; requires 100"
    )


def test_drift_cli_malformed_baseline_exits_two(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from tests.eval.test_drift import _seed_drift

    path = tmp_path / "drift.sqlite3"
    _seed_drift(path)
    assert main([
        "--database", str(path), "drift", "baseline", "--agent", "agent-a",
        *LEGACY_DRIFT_POLICY_ARGS, *DRIFT_WINDOW_ARGS,
    ]) == 0
    capsys.readouterr()
    database = Database.open(path)
    try:
        database.connection.execute("UPDATE drift_baselines SET reference_json = '{}' ")
        database.connection.commit()
    finally:
        database.close()
    assert main([
        "--database", str(path), "drift", "--agent", "agent-a", *LEGACY_DRIFT_POLICY_ARGS
    ]) == 2
    assert capsys.readouterr().err.strip() == "glassbox: unable to run drift command"


@pytest.mark.parametrize(
    "malformation",
    (
        "empty_histogram_counts",
        "histogram_dimensions",
        "negative_histogram_count",
        "nonfinite_trace_mean",
        "overflow_trace_mean",
        "trace_count_semantics",
        "reference_not_mapping",
        "versions_not_mapping",
        "negative_version_count",
        "invalid_decisions_per_trace",
    ),
)
def test_drift_malformed_saved_material_is_policy_error_and_cli_exit_two(
    tmp_path: Path, capsys, malformation: str
) -> None:  # type: ignore[no-untyped-def]
    from glassbox.cli import main
    from glassbox.eval.drift import DriftPolicyError, load_policy, run_drift_report
    from tests.eval.test_drift import AS_OF, POLICY_PATH, _seed_drift

    path = tmp_path / "drift.sqlite3"
    _seed_drift(path)
    assert main([
        "--database", str(path), "drift", "baseline", "--agent", "agent-a",
        *LEGACY_DRIFT_POLICY_ARGS, *DRIFT_WINDOW_ARGS,
    ]) == 0
    capsys.readouterr()
    database = Database.open(path)
    try:
        connection = database.connection
        reference = json.loads(
            connection.execute("SELECT reference_json FROM drift_baselines").fetchone()[0]
        )
        versions = json.loads(
            connection.execute("SELECT version_counts_json FROM drift_baselines").fetchone()[0]
        )
        if malformation == "empty_histogram_counts":
            reference["confidence"]["counts"] = []
        elif malformation == "histogram_dimensions":
            reference["confidence"]["labels"] = []
        elif malformation == "negative_histogram_count":
            reference["confidence"]["counts"][0] = -1
        elif malformation == "nonfinite_trace_mean":
            reference["trace_latency_ms"]["mean"] = "nan"
        elif malformation == "overflow_trace_mean":
            reference["trace_latency_ms"]["mean"] = 10**1000
        elif malformation == "trace_count_semantics":
            reference["trace_latency_ms"]["count"] = 0
        elif malformation == "reference_not_mapping":
            reference["confidence"] = []
        elif malformation == "versions_not_mapping":
            versions["versions"] = []
        elif malformation == "negative_version_count":
            versions["versions"]["v1"]["traces"] = -1
        else:
            versions["decisions_per_trace"] = -1
        connection.execute(
            "UPDATE drift_baselines SET reference_json = ?, version_counts_json = ?",
            (json.dumps(reference), json.dumps(versions)),
        )
        connection.commit()
    finally:
        database.close()

    with pytest.raises(DriftPolicyError, match="stored drift baseline"):
        run_drift_report(
            path, "agent-a", load_policy(POLICY_PATH), clock=lambda: AS_OF, persist=False
        )
    assert main([
        "--database", str(path), "drift", "--agent", "agent-a", *LEGACY_DRIFT_POLICY_ARGS
    ]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "glassbox: unable to run drift command"


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
        *DRIFT_WINDOW_ARGS,
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
