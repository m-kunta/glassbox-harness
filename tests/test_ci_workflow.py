"""Static contracts for the credential-free, trusted-base judge gate."""

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CI_ROOT = PROJECT_ROOT / ".glassbox" / "ci"
WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "calibrated-judge-gate.yml"


def test_judge_gate_policy_is_the_approved_seven_day_contract() -> None:
    assert tomllib.loads((CI_ROOT / "judge-gate-policy.toml").read_text()) == {
        "format_version": "judge_gate_policy_v1",
        "max_age_seconds": 604800,
        "expected_report_format": "judge_gate_report_v1",
        "expected_rubric_version": "reasoning_quality_v1",
    }


def test_judge_report_example_is_documentation_without_live_evidence() -> None:
    example = json.loads((CI_ROOT / "judge-report.json.example").read_text())
    assert example == {
        "format_version": "judge_gate_report_v1",
        "status": "uncalibrated",
        "reason": "too_few_labels",
        "generated_at": "GENERATED_AT_UTC",
        "provider": "ollama",
        "model": "llama3.1:8b",
        "rubric_version": "reasoning_quality_v1",
    }


def test_gitignore_allows_tracking_operator_ci_files() -> None:
    patterns = (PROJECT_ROOT / ".gitignore").read_text().splitlines()
    assert ".glassbox/" not in patterns
    for pattern in (
        ".glassbox/*",
        "!.glassbox/ci/",
        "!.glassbox/ci/judge-gate-policy.toml",
        "!.glassbox/ci/judge-report.json",
    ):
        assert pattern in patterns


def test_workflow_runs_only_the_trusted_base_verifier() -> None:
    workflow = WORKFLOW.read_text()
    for required in (
        "pull_request_target:",
        "push:",
        "workflow_dispatch:",
        "permissions:\n  contents: read",
        "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683",
        "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
        "ref: ${{ github.event.pull_request.base.sha || github.sha }}",
        "persist-credentials: false",
        'python-version: "3.11"',
        "python -m pip install .",
        "python -m glassbox judge verify-report --report .glassbox/ci/judge-report.json "
        "--policy .glassbox/ci/judge-gate-policy.toml",
    ):
        assert required in workflow
    for forbidden in (
        "secrets", "GLASSBOX_", "judge --since", "--confirm-egress", "sqlite",
        "artifact", "cache", "pull_request:", "pull_request.head", "refs/pull/",
        "judge-report.json.example",
    ):
        assert forbidden not in workflow


def test_workflow_publishes_summary_and_preserves_verifier_exit_code() -> None:
    workflow = WORKFLOW.read_text()
    assert "> judge-gate-result.json" in workflow
    assert "set +e" in workflow
    assert "status=$?" in workflow
    assert "## Calibrated judge gate" in workflow
    assert "cat judge-gate-result.json" in workflow
    assert '>> "$GITHUB_STEP_SUMMARY"' in workflow
    assert 'exit "$status"' in workflow


def test_workflow_permissions_and_checkout_are_scoped_to_trusted_code() -> None:
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    assert set(workflow["on"]) == {"pull_request_target", "push", "workflow_dispatch"}
    assert workflow["on"]["push"] == {"branches": ["main"]}
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"judge-gate"}
    job = workflow["jobs"]["judge-gate"]
    assert "permissions" not in job
    checkout = job["steps"][0]
    assert checkout["with"] == {
        "ref": "${{ github.event.pull_request.base.sha || github.sha }}",
        "persist-credentials": "false",
    }
    assert len(job["steps"]) == 4


def test_workflow_missing_evidence_keeps_exit_two_and_explains_refresh(tmp_path: Path) -> None:
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    script = workflow["jobs"]["judge-gate"]["steps"][-1]["run"]
    summary = tmp_path / "summary.md"
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{PROJECT_ROOT / '.venv' / 'bin'}:{os.environ.get('PATH', '')}",
            "PYTHONPATH": str(PROJECT_ROOT),
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert result.stderr == "glassbox: unable to verify judge report\n"
    assert (tmp_path / "judge-gate-result.json").read_text() == ""
    assert "## Calibrated judge gate" in summary.read_text()
    assert "Refresh" in summary.read_text()


def test_python_module_entrypoint_runs_the_verifier(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable, "-m", "glassbox", "judge", "verify-report",
            "--report", str(tmp_path / "absent-report.json"),
            "--policy", str(CI_ROOT / "judge-gate-policy.toml"),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr == "glassbox: unable to verify judge report\n"
