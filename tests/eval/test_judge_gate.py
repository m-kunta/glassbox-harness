"""Pure, credential-free validation of precomputed judge gate evidence."""

import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from glassbox.eval.judge_gate import JudgeGateError, verify_judge_gate_report

REPORT = {
    "format_version": "judge_gate_report_v1",
    "status": "uncalibrated",
    "reason": "too_few_labels",
    "generated_at": "2026-11-10T12:00:00Z",
    "provider": "ollama",
    "model": "llama3.2",
    "rubric_version": "reasoning_quality_v1",
}
POLICY = """format_version = "judge_gate_policy_v1"
max_age_seconds = 604800
expected_report_format = "judge_gate_report_v1"
expected_rubric_version = "reasoning_quality_v1"
"""
NOW = datetime(2026, 11, 10, 12, tzinfo=UTC)
PRIVATE = "PRIVATE_DECISION_CONTENT"


def _report(
    tmp_path: Path,
    status: str = "uncalibrated",
    reason: str = "too_few_labels",
    **changes: Any,
) -> Path:
    path = tmp_path / "private-report.json"
    path.write_text(
        json.dumps(REPORT | {"status": status, "reason": reason} | changes), encoding="utf-8"
    )
    return path


def _policy(tmp_path: Path, text: str = POLICY) -> Path:
    path = tmp_path / "private-policy.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _assert_safe_error(report: Path, policy: Path, now: Any = NOW) -> None:
    with pytest.raises(JudgeGateError) as exc:
        verify_judge_gate_report(report, policy, clock=lambda: now)
    message = str(exc.value)
    assert message
    assert str(report) not in message
    assert str(policy) not in message
    assert report.name not in message
    assert policy.name not in message
    assert PRIVATE not in message
    assert "judge_gate_report_v1" not in message
    assert exc.value.__suppress_context__


def test_valid_statuses_have_exact_exit_categories(tmp_path: Path) -> None:
    for status, reason, exit_code in [
        ("passed", "calibrated", 0),
        ("uncalibrated", "too_few_labels", 0),
        ("failed", "mean_score_below_threshold", 1),
    ]:
        result = verify_judge_gate_report(
            _report(tmp_path, status, reason), _policy(tmp_path), clock=lambda: NOW
        )
        assert result.exit_code == exit_code
        assert result.status == status
        assert result.reason == reason


@pytest.mark.parametrize(
    "reason",
    [
        "too_few_labels",
        "kappa_undefined",
        "kappa_below_threshold",
        "gated_set_too_small",
        "failure_rate_too_high",
    ],
)
def test_all_uncalibrated_reasons_are_neutral(tmp_path: Path, reason: str) -> None:
    result = verify_judge_gate_report(
        _report(tmp_path, reason=reason), _policy(tmp_path), clock=lambda: NOW
    )
    assert result.reason == reason
    assert result.exit_code == 0


def test_output_is_exact_safe_and_immutable(tmp_path: Path) -> None:
    report = _report(tmp_path)
    policy = _policy(tmp_path)
    result = verify_judge_gate_report(report, policy, clock=lambda: NOW)
    assert result.to_dict() == {
        "status": "uncalibrated",
        "reason": "too_few_labels",
        "generated_at": "2026-11-10T12:00:00Z",
        "age_seconds": 0,
        "provider": "ollama",
        "model": "llama3.2",
        "rubric_version": "reasoning_quality_v1",
    }
    with pytest.raises(FrozenInstanceError):
        result.status = "failed"  # type: ignore[misc]
    result.to_dict()["status"] = "failed"
    assert result.status == "uncalibrated"
    _assert_safe_error(_report(tmp_path, private_decision=PRIVATE), policy)
    assert PRIVATE not in json.dumps(result.to_dict())


@pytest.mark.parametrize("key", REPORT)
def test_missing_report_fields_are_rejected(tmp_path: Path, key: str) -> None:
    data = dict(REPORT)
    del data[key]
    report = _report(tmp_path)
    report.write_text(json.dumps(data), encoding="utf-8")
    _assert_safe_error(report, _policy(tmp_path))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("status", "unknown"),
        ("status", None),
        ("status", []),
        ("reason", PRIVATE),
        ("reason", None),
        ("reason", []),
        ("format_version", "judge_gate_report_v2"),
        ("format_version", None),
        ("rubric_version", "other_rubric"),
        ("generated_at", None),
        ("generated_at", 123),
        ("generated_at", "2026-11-10"),
        ("generated_at", "2026-11-10T12:00:00+00:00"),
        ("generated_at", "2026-11-10T12:00:00+01:00"),
        ("generated_at", "2026-11-10T12:00:00"),
        ("generated_at", "2026-11-10 12:00:00Z"),
        ("generated_at", "2026-11-10T12:00:00z"),
        ("generated_at", "2026-11-10T12:00:00.1234567Z"),
        ("generated_at", "2026-11-10T12:00:00.Z"),
        ("generated_at", "2026-02-30T12:00:00Z"),
        ("generated_at", "2026-11-10T24:00:00Z"),
        ("generated_at", "2026-11-10T12:00:60Z"),
        ("generated_at", "2026-11-10T12:00:00Z\n"),
        ("generated_at", "2026-11-10T12:00:00.000001Z"),
        ("generated_at", "2026-11-03T11:59:59.999999Z"),
    ],
)
def test_invalid_report_values_are_rejected(tmp_path: Path, key: str, value: Any) -> None:
    _assert_safe_error(_report(tmp_path, **{key: value}), _policy(tmp_path))


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        ("passed", "too_few_labels"),
        ("passed", "mean_score_below_threshold"),
        ("failed", "calibrated"),
        ("failed", "too_few_labels"),
        ("uncalibrated", "calibrated"),
        ("uncalibrated", "mean_score_below_threshold"),
    ],
)
def test_mismatched_status_reason_pairs_are_rejected(
    tmp_path: Path,
    status: str,
    reason: str,
) -> None:
    _assert_safe_error(_report(tmp_path, status, reason), _policy(tmp_path))


@pytest.mark.parametrize("key", ["provider", "model", "rubric_version"])
@pytest.mark.parametrize(
    "value",
    [
        None,
        42,
        True,
        [],
        {},
        "",
        " ",
        "with space",
        "with\nnewline",
        "with\ttab",
        "mödél",
        "<script>",
        "https://host?token=secret",
        "$(command)",
        "a" * 129,
    ],
)
def test_invalid_provenance_is_rejected(tmp_path: Path, key: str, value: Any) -> None:
    _assert_safe_error(_report(tmp_path, **{key: value}), _policy(tmp_path))


@pytest.mark.parametrize("model", ["Org/model-v1.2:latest+Q4_K_M", "a" * 128])
def test_ascii_model_identifiers_are_accepted(tmp_path: Path, model: str) -> None:
    result = verify_judge_gate_report(
        _report(tmp_path, model=model), _policy(tmp_path), clock=lambda: NOW
    )
    assert result.model == model


@pytest.mark.parametrize(
    "text",
    [
        PRIVATE,
        "[]",
        "null",
        '"PRIVATE_DECISION_CONTENT"',
        "{",
        "{}",
    ],
)
def test_invalid_json_or_root_shape_is_rejected(tmp_path: Path, text: str) -> None:
    report = _report(tmp_path)
    report.write_text(text, encoding="utf-8")
    _assert_safe_error(report, _policy(tmp_path))


@pytest.mark.parametrize(
    "text",
    [
        "PRIVATE_DECISION_CONTENT = [",
        "",
        "[]",
        POLICY + 'unknown = "PRIVATE_DECISION_CONTENT"\n',
        POLICY.replace('format_version = "judge_gate_policy_v1"\n', ""),
        POLICY.replace("max_age_seconds = 604800\n", ""),
        POLICY.replace('expected_report_format = "judge_gate_report_v1"\n', ""),
        POLICY.replace('expected_rubric_version = "reasoning_quality_v1"\n', ""),
        POLICY.replace("judge_gate_policy_v1", "judge_gate_policy_v2"),
        POLICY.replace("judge_gate_report_v1", "judge_gate_report_v2"),
        POLICY.replace('"reasoning_quality_v1"', '"wrong rubric"'),
        POLICY.replace("604800", "true"),
        POLICY.replace("604800", '"604800"'),
        POLICY.replace("604800", "604800.0"),
        POLICY.replace("604800", "-1"),
    ],
)
def test_invalid_policy_is_rejected(tmp_path: Path, text: str) -> None:
    _assert_safe_error(_report(tmp_path), _policy(tmp_path, text))


@pytest.mark.parametrize("file_kind", ["report", "policy"])
@pytest.mark.parametrize("problem", ["missing", "directory", "invalid_utf8"])
def test_unreadable_files_fail_safely(tmp_path: Path, file_kind: str, problem: str) -> None:
    report = _report(tmp_path)
    policy = _policy(tmp_path)
    path = report if file_kind == "report" else policy
    path.unlink()
    if problem == "directory":
        path.mkdir()
    elif problem == "invalid_utf8":
        path.write_bytes(b"\xffPRIVATE_DECISION_CONTENT")
    _assert_safe_error(report, policy)


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 11, 10, 12),
        datetime(2026, 11, 10, 12, tzinfo=timezone(timedelta(hours=1))),
        None,
        "2026-11-10T12:00:00Z",
    ],
)
def test_clock_must_return_a_utc_aware_datetime(tmp_path: Path, now: Any) -> None:
    _assert_safe_error(_report(tmp_path), _policy(tmp_path), now)


def test_freshness_uses_one_clock_value_and_inclusive_maximum(tmp_path: Path) -> None:
    calls = 0

    def clock() -> datetime:
        nonlocal calls
        calls += 1
        return NOW + timedelta(seconds=604800)

    result = verify_judge_gate_report(_report(tmp_path), _policy(tmp_path), clock=clock)
    assert calls == 1
    assert result.age_seconds == 604800
    _assert_safe_error(
        _report(tmp_path), _policy(tmp_path), NOW + timedelta(seconds=604800, microseconds=1)
    )


@pytest.mark.parametrize(
    ("timestamp", "normalized", "age"),
    [
        ("2026-11-10T11:59:59.1Z", "2026-11-10T11:59:59.100000Z", 0),
        ("2026-11-10T11:59:58.123456Z", "2026-11-10T11:59:58.123456Z", 1),
        ("2026-11-10T12:00:00.000000Z", "2026-11-10T12:00:00Z", 0),
    ],
)
def test_fractional_timestamps_are_normalized(
    tmp_path: Path,
    timestamp: str,
    normalized: str,
    age: int,
) -> None:
    result = verify_judge_gate_report(
        _report(tmp_path, generated_at=timestamp), _policy(tmp_path), clock=lambda: NOW
    )
    assert result.generated_at == normalized
    assert result.age_seconds == age
