"""Validate precomputed judge gate evidence using only local files and a clock."""

import json
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, cast

_REPORT_FORMAT = "judge_gate_report_v1"
_POLICY_FORMAT = "judge_gate_policy_v1"
_REPORT_KEYS = frozenset(
    {
        "format_version",
        "status",
        "reason",
        "generated_at",
        "provider",
        "model",
        "rubric_version",
    }
)
_POLICY_KEYS = frozenset(
    {
        "format_version",
        "max_age_seconds",
        "expected_report_format",
        "expected_rubric_version",
    }
)
_REASONS = {
    "passed": frozenset({"calibrated"}),
    "failed": frozenset({"mean_score_below_threshold"}),
    "uncalibrated": frozenset(
        {
            "too_few_labels",
            "kappa_undefined",
            "kappa_below_threshold",
            "gated_set_too_small",
            "failure_rate_too_high",
        }
    ),
}
_IDENTIFIER = re.compile(r"[A-Za-z0-9._:+/-]{1,128}")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", re.ASCII)


class JudgeGateError(ValueError):
    """Safe failure while validating a precomputed judge gate report."""


@dataclass(frozen=True)
class JudgeGateResult:
    status: Literal["passed", "failed", "uncalibrated"]
    reason: str
    generated_at: str
    age_seconds: int
    provider: str
    model: str
    rubric_version: str

    @property
    def exit_code(self) -> int:
        return 1 if self.status == "failed" else 0

    def to_dict(self) -> dict[str, str | int]:
        """Return only validated summary fields, never the input document."""
        return {
            "status": self.status,
            "reason": self.reason,
            "generated_at": self.generated_at,
            "age_seconds": self.age_seconds,
            "provider": self.provider,
            "model": self.model,
            "rubric_version": self.rubric_version,
        }


@dataclass(frozen=True)
class _JudgeGatePolicy:
    max_age_seconds: int
    expected_report_format: str
    expected_rubric_version: str


def _exact_fields(value: object, keys: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, dict) or value.keys() != keys:
        raise ValueError
    return cast(dict[str, object], value)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError
    return value


def _identifier(value: object) -> str:
    text = _string(value)
    if _IDENTIFIER.fullmatch(text) is None:
        raise ValueError
    return text


def _parse_policy(text: str) -> _JudgeGatePolicy:
    fields = _exact_fields(tomllib.loads(text), _POLICY_KEYS)
    if fields["format_version"] != _POLICY_FORMAT:
        raise ValueError
    expected_format = _string(fields["expected_report_format"])
    if expected_format != _REPORT_FORMAT:
        raise ValueError
    max_age = fields["max_age_seconds"]
    if type(max_age) is not int or max_age < 0:
        raise ValueError
    return _JudgeGatePolicy(
        max_age_seconds=max_age,
        expected_report_format=expected_format,
        expected_rubric_version=_identifier(fields["expected_rubric_version"]),
    )


def verify_judge_gate_report(
    report_path: Path,
    policy_path: Path,
    *,
    clock: Callable[[], datetime],
) -> JudgeGateResult:
    """Check strict schema, provenance, and freshness without executing a judge."""
    try:
        policy = _parse_policy(policy_path.read_text(encoding="utf-8"))
        fields = _exact_fields(json.loads(report_path.read_text(encoding="utf-8")), _REPORT_KEYS)
        if fields["format_version"] != policy.expected_report_format:
            raise ValueError
        status = _string(fields["status"])
        reason = _string(fields["reason"])
        if status not in _REASONS or reason not in _REASONS[status]:
            raise ValueError
        provider = _identifier(fields["provider"])
        model = _identifier(fields["model"])
        rubric = _identifier(fields["rubric_version"])
        if rubric != policy.expected_rubric_version:
            raise ValueError
        timestamp = _string(fields["generated_at"])
        if _TIMESTAMP.fullmatch(timestamp) is None:
            raise ValueError
        generated_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        now = clock()
        if not isinstance(now, datetime) or now.utcoffset() != timedelta(0):
            raise ValueError
        age = (now - generated_at).total_seconds()
        if age < 0 or age > policy.max_age_seconds:
            raise ValueError
        return JudgeGateResult(
            status=cast(Literal["passed", "failed", "uncalibrated"], status),
            reason=reason,
            generated_at=generated_at.isoformat().replace("+00:00", "Z"),
            age_seconds=int(age),
            provider=provider,
            model=model,
            rubric_version=rubric,
        )
    except (OSError, TypeError, ValueError):
        # JSON/TOML/Unicode decoding errors are ValueErrors. Never retain their
        # document snippets or OS path details in the public diagnostic.
        raise JudgeGateError("Unable to verify judge report") from None
