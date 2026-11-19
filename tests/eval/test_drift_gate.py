import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from glassbox.eval.drift_gate import DriftGateError, verify_drift_gate_report

NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)
REPORT = {
    "format_version": "drift_gate_report_v1",
    "status": "healthy",
    "reason": None,
    "generated_at": "2026-11-17T12:00:00Z",
    "agent_name": "replenishment-triage-ai",
    "policy_version": "drift_v2",
    "policy_hash": "a" * 64,
    "baseline_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    "signals": [
        {
            "name": "confidence",
            "status": "healthy",
            "reason": None,
            "baseline_count": 500,
            "recent_count": 200,
        },
        {
            "name": "decision_type",
            "status": "watch",
            "reason": None,
            "baseline_count": 500,
            "recent_count": 200,
        },
    ],
}
POLICY = """format_version = "drift_gate_policy_v1"
max_age_seconds = 604800
expected_report_format = "drift_gate_report_v1"
expected_agent_name = "replenishment-triage-ai"
expected_policy_version = "drift_v2"
expected_signals = ["confidence", "decision_type"]
"""
ERROR = "^Unable to verify drift report$"


@pytest.fixture
def evidence(tmp_path: Path) -> tuple[Path, Path]:
    report = tmp_path / "report.json"
    policy = tmp_path / "policy.toml"
    report.write_text(json.dumps(REPORT), encoding="utf-8")
    policy.write_text(POLICY, encoding="utf-8")
    return report, policy


@pytest.mark.parametrize(
    ("status", "reason", "exit_code"),
    [
        ("healthy", None, 0),
        ("watch", None, 0),
        ("insufficient_data", "recent_window_too_small", 0),
        ("drift_detected", None, 1),
    ],
)
def test_valid_report_returns_exact_summary(
    evidence: tuple[Path, Path], status: str, reason: str | None, exit_code: int
) -> None:
    report, policy = evidence
    payload = {**REPORT, "status": status, "reason": reason}
    report.write_text(json.dumps(payload), encoding="utf-8")

    result = verify_drift_gate_report(report, policy, clock=lambda: NOW)

    assert result.exit_code == exit_code
    assert result.to_dict() == {**payload, "age_seconds": 0}


def test_insufficient_data_accepts_null_baseline(evidence: tuple[Path, Path]) -> None:
    report, policy = evidence
    payload = {
        **REPORT,
        "status": "insufficient_data",
        "reason": "baseline_not_created",
        "baseline_id": None,
    }
    report.write_text(json.dumps(payload), encoding="utf-8")

    result = verify_drift_gate_report(report, policy, clock=lambda: NOW)

    assert result.to_dict()["baseline_id"] is None


def test_report_at_max_age_passes(evidence: tuple[Path, Path]) -> None:
    report, policy = evidence
    result = verify_drift_gate_report(report, policy, clock=lambda: NOW + timedelta(days=7))

    assert result.to_dict()["age_seconds"] == 604800


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("extra", "PRIVATE_DECISION_CONTENT"),
        ("format_version", "another_report_v1"),
        ("reason", "recent_window_too_small"),
        ("reason", ["PRIVATE_DECISION_CONTENT"]),
        ("status", "insufficient_data"),
        ("status", "unknown"),
        ("generated_at", "2026-11-17T12:00:00+00:00"),
        ("generated_at", "2026-11-17T12:00:00.000001Z"),
        ("generated_at", "2026-11-10T11:59:59.999999Z"),
        ("policy_version", "drift_v1"),
        ("agent_name", "another-agent"),
        ("policy_hash", "A" * 64),
        ("policy_hash", "a" * 63),
        ("baseline_id", None),
        ("baseline_id", "a" * 129),
        ("baseline_id", "café"),
        ("signals", []),
        ("signals", REPORT["signals"][::-1]),
        ("signals", [REPORT["signals"][0]]),
        ("signals", [REPORT["signals"][0], REPORT["signals"][0]]),
    ],
)
def test_invalid_report_field_has_generic_error(
    evidence: tuple[Path, Path], key: str, value: object
) -> None:
    report, policy = evidence
    report.write_text(json.dumps({**REPORT, key: value}), encoding="utf-8")

    with pytest.raises(DriftGateError, match=ERROR):
        verify_drift_gate_report(report, policy, clock=lambda: NOW)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("name", "unknown_signal"),
        ("reason", "PRIVATE_DECISION_CONTENT"),
        ("status", "insufficient_data"),
        ("baseline_count", 0),
        ("recent_count", True),
        ("recent_count", 2.5),
        ("extra", "PRIVATE_DECISION_CONTENT"),
    ],
)
def test_invalid_signal_field_has_generic_error(
    evidence: tuple[Path, Path], key: str, value: object
) -> None:
    report, policy = evidence
    payload = copy.deepcopy(REPORT)
    payload["signals"][0][key] = value
    report.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(DriftGateError, match=ERROR):
        verify_drift_gate_report(report, policy, clock=lambda: NOW)


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        ('"status": "healthy"', '"status": "healthy", "status": "healthy"'),
        ('"name": "confidence"', '"name": "confidence", "name": "confidence"'),
        ('"reason": null, ', ""),
    ],
)
def test_rejects_duplicate_or_missing_json_keys(
    evidence: tuple[Path, Path], original: str, replacement: str
) -> None:
    report, policy = evidence
    report.write_text(json.dumps(REPORT).replace(original, replacement, 1), encoding="utf-8")

    with pytest.raises(DriftGateError, match=ERROR):
        verify_drift_gate_report(report, policy, clock=lambda: NOW)


@pytest.mark.parametrize(
    "contents",
    [
        POLICY + 'extra = "PRIVATE_DECISION_CONTENT"\n',
        POLICY + 'expected_agent_name = "PRIVATE_DECISION_CONTENT"\n',
        POLICY.replace("604800", "true"),
        POLICY.replace("604800", "-1"),
        POLICY.replace('"confidence", "decision_type"', '"confidence", "confidence"'),
        POLICY.replace('"confidence", "decision_type"', ""),
        POLICY.replace("drift_gate_policy_v1", "drift_gate_policy_v2"),
        POLICY.replace("drift_gate_report_v1", "drift_gate_report_v2"),
        POLICY.replace('expected_policy_version = "drift_v2"\n', ""),
        "PRIVATE_DECISION_CONTENT",
    ],
)
def test_invalid_policy_has_generic_error(evidence: tuple[Path, Path], contents: str) -> None:
    report, policy = evidence
    policy.write_text(contents, encoding="utf-8")

    with pytest.raises(DriftGateError, match=ERROR):
        verify_drift_gate_report(report, policy, clock=lambda: NOW)


@pytest.mark.parametrize("file_index", [0, 1], ids=["report", "policy"])
def test_missing_file_has_generic_error(evidence: tuple[Path, Path], file_index: int) -> None:
    report, policy = evidence
    evidence[file_index].unlink()

    with pytest.raises(DriftGateError, match=ERROR):
        verify_drift_gate_report(report, policy, clock=lambda: NOW)


def test_malformed_report_has_generic_error(evidence: tuple[Path, Path]) -> None:
    report, policy = evidence
    report.write_text('{"PRIVATE_DECISION_CONTENT":', encoding="utf-8")

    with pytest.raises(DriftGateError, match=ERROR):
        verify_drift_gate_report(report, policy, clock=lambda: NOW)


def test_naive_clock_has_generic_error(evidence: tuple[Path, Path]) -> None:
    report, policy = evidence

    with pytest.raises(DriftGateError, match=ERROR):
        verify_drift_gate_report(report, policy, clock=lambda: NOW.replace(tzinfo=None))
