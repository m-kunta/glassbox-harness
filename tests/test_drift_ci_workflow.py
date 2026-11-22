"""Contract tests for the trusted-base drift evidence workflow."""

import json
import os
import subprocess
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_policy_and_example() -> None:
    ci = ROOT / ".glassbox/ci"
    assert tomllib.loads((ci / "drift-gate-policy.toml").read_text()) == {
        "format_version": "drift_gate_policy_v1", "max_age_seconds": 604800,
        "expected_report_format": "drift_gate_report_v1",
        "expected_agent_name": "replenishment-triage-ai", "expected_policy_version": "drift_v2",
        "expected_signals": ["confidence", "decision_type"],
    }
    example = json.loads((ci / "drift-report.json.example").read_text())
    assert example["generated_at"] == "GENERATED_AT_UTC"


def test_workflow_trust_and_missing_evidence(tmp_path: Path) -> None:
    text = (ROOT / ".github/workflows/drift-evidence-gate.yml").read_text()
    workflow = yaml.load(text, Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"pull_request_target", "push", "workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    steps = workflow["jobs"]["drift-gate"]["steps"]
    assert steps[0]["with"] == {"ref": "${{ github.event.pull_request.base.sha || github.sha }}",
                                "persist-credentials": "false"}
    for forbidden in ("secrets", "GLASSBOX_", "sqlite", "artifact", "cache", "discord",
                      "pull_request.head", "drift-report.json.example"):
        assert forbidden not in text
    summary = tmp_path / "summary"
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", steps[-1]["run"]], cwd=tmp_path,
        env={**os.environ, "PATH": f"{ROOT / '.venv/bin'}:{os.environ.get('PATH', '')}",
             "PYTHONPATH": str(ROOT), "GITHUB_STEP_SUMMARY": str(summary)},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert result.stderr == "glassbox: unable to verify drift report\n"
    assert "Refresh" in summary.read_text()
