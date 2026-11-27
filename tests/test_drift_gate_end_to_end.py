"""Synthetic-only integration tests from drift CLI to compact gate evidence."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from glassbox import cli
from glassbox.eval.discord_config import DiscordConfig
from glassbox.store import Database
from tests.eval.test_drift import _seed_drift

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("baseline_count", "recent_count", "shift", "expected", "gate_exit"),
    [(0, 200, False, "baseline_not_created", 0),
     (499, 200, False, "baseline_rejected", None),
     (500, 199, False, "recent_window_too_small", 0),
     (500, 200, False, "healthy", 0),
     (500, 200, False, "policy_changed_requires_rebaseline", 0),
     (500, 200, True, "drift_detected", 1)],
)
def test_synthetic_cli_to_gate(
    tmp_path, monkeypatch, capsys, baseline_count, recent_count, shift, expected, gate_exit
):
    monkeypatch.setattr(
        "glassbox.eval.discord_config.load_discord_config",
        lambda *_: DiscordConfig("disabled", None, None, "missing_url"),
    )
    path = tmp_path / "SYNTHETIC.sqlite3"
    at = (datetime.now(UTC) - timedelta(days=2)).replace(microsecond=0)
    _seed_drift(path, baseline_decisions=baseline_count, recent_at=at)
    db = Database.open(path)
    try:
        db.connection.execute("DELETE FROM decisions WHERE agent_version = 'v2'")
        for i in range(recent_count):
            db.connection.execute(
                "INSERT INTO decisions (decision_id, trace_id, agent_name, agent_version, "
                "entity_type, entity_id, decision_type, recommendation, rationale, "
                "rationale_citations, confidence, alternatives_considered, decided_at) "
                "VALUES (?, ?, 'agent-a', 'v2', 'sku', 'synthetic', ?, '{}', 'synthetic', "
                "'[]', ?, '[]', ?)",
                (f"{i + 5000:026X}", f"{i % 20 + 2000:026X}",
                 "order" if shift else "review", 0.95 if shift else 0.05,
                 at.isoformat().replace("+00:00", "Z")),
            )
        db.connection.commit()
    finally:
        db.close()

    prefix = ["--database", str(path), "drift"]
    baseline_hash = None
    if baseline_count:
        result = cli.main([*prefix, "baseline", "--agent", "agent-a", "--start",
                           "2026-01-01T00:00:00Z", "--end", "2026-06-01T00:00:00Z"])
        baseline_output = capsys.readouterr()
        if expected == "baseline_rejected":
            assert result == 2
            return
        assert result == 0
        baseline_hash = json.loads(baseline_output.out)["policy_hash"]

    policy_args = []
    if expected == "policy_changed_requires_rebaseline":
        changed_policy = tmp_path / "changed-policy.toml"
        changed_policy.write_text(
            (ROOT / "glassbox/eval/policies/drift_v2.toml").read_text().replace(
                "warning_threshold = 0.10", "warning_threshold = 0.11"
            )
        )
        policy_args = ["--policy", str(changed_policy)]
    assert cli.main([*prefix, "--agent", "agent-a", *policy_args]) == 0
    drift = json.loads(capsys.readouterr().out)
    assert (drift["reason"] or drift["status"]) == expected
    if policy_args:
        assert drift["policy_hash"] != baseline_hash
        assert drift["baseline_id"] is None
    compact = {key: drift[key] for key in ("agent_name", "policy_version", "policy_hash",
                                            "baseline_id", "status", "reason")}
    compact.update(format_version="drift_gate_report_v1",
                   generated_at=drift["as_of"].replace("+00:00", "Z"))
    compact["signals"] = [
        {key: drift["signals"][name][key] for key in
         ("name", "status", "reason", "baseline_count", "recent_count")}
        for name in ("confidence", "decision_type")
    ]
    report = tmp_path / "SYNTHETIC-report.json"
    report.write_text(json.dumps(compact))
    policy = tmp_path / "policy.toml"
    policy.write_text((ROOT / ".glassbox/ci/drift-gate-policy.toml").read_text().replace(
        '"replenishment-triage-ai"', '"agent-a"'))
    assert cli.main(["drift", "verify-report", "--report", str(report),
                     "--policy", str(policy)]) == gate_exit
    assert json.loads(capsys.readouterr().out)["status"] == drift["status"]
