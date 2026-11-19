"""Validate compact drift gate evidence using only local files and a clock."""

import json
import re
import tomllib
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, cast

_REPORT_FORMAT = "drift_gate_report_v1"
_POLICY_FORMAT = "drift_gate_policy_v1"
_REPORT_KEYS = frozenset(
    {
        "format_version",
        "status",
        "reason",
        "generated_at",
        "agent_name",
        "policy_version",
        "policy_hash",
        "baseline_id",
        "signals",
    }
)
_SIGNAL_KEYS = frozenset({"name", "status", "reason", "baseline_count", "recent_count"})
_POLICY_KEYS = frozenset(
    {
        "format_version",
        "max_age_seconds",
        "expected_report_format",
        "expected_agent_name",
        "expected_policy_version",
        "expected_signals",
    }
)
_DriftStatus = Literal["healthy", "watch", "drift_detected", "insufficient_data"]
_REASONS: dict[str, frozenset[str | None]] = {
    "healthy": frozenset({None}),
    "watch": frozenset({None}),
    "drift_detected": frozenset({None}),
    "insufficient_data": frozenset(
        {
            "baseline_not_created",
            "policy_changed_requires_rebaseline",
            "baseline_window_too_small",
            "recent_window_too_small",
            "baseline_has_no_variance",
        }
    ),
}
_IDENTIFIER = re.compile(r"[A-Za-z0-9._:+/-]{1,128}")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", re.ASCII)
_POLICY_HASH = re.compile(r"[0-9a-f]{64}")


class DriftGateError(ValueError):
    """Safe failure while validating precomputed drift gate evidence."""


@dataclass(frozen=True)
class _DriftGateSignal:
    name: str
    status: _DriftStatus
    reason: str | None
    baseline_count: int
    recent_count: int


@dataclass(frozen=True)
class DriftGateResult:
    format_version: str
    status: _DriftStatus
    reason: str | None
    generated_at: str
    agent_name: str
    policy_version: str
    policy_hash: str
    baseline_id: str | None
    signals: tuple[_DriftGateSignal, ...]
    age_seconds: int

    @property
    def exit_code(self) -> int:
        return 1 if self.status == "drift_detected" else 0

    def to_dict(self) -> dict[str, object]:
        """Return only validated report fields and the report age."""
        return {
            "format_version": self.format_version,
            "status": self.status,
            "reason": self.reason,
            "generated_at": self.generated_at,
            "agent_name": self.agent_name,
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
            "baseline_id": self.baseline_id,
            "signals": [asdict(signal) for signal in self.signals],
            "age_seconds": self.age_seconds,
        }


@dataclass(frozen=True)
class _DriftGatePolicy:
    max_age_seconds: int
    expected_agent_name: str
    expected_policy_version: str
    expected_signals: tuple[str, ...]


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    fields: dict[str, object] = {}
    for key, value in pairs:
        if key in fields:
            raise ValueError
        fields[key] = value
    return fields


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


def _status_reason(fields: dict[str, object]) -> tuple[_DriftStatus, str | None]:
    status = _string(fields["status"])
    reason = fields["reason"]
    if reason is not None:
        reason = _string(reason)
    if status not in _REASONS or reason not in _REASONS[status]:
        raise ValueError
    return cast(_DriftStatus, status), reason


def _parse_policy(text: str) -> _DriftGatePolicy:
    fields = _exact_fields(tomllib.loads(text), _POLICY_KEYS)
    if (
        fields["format_version"] != _POLICY_FORMAT
        or fields["expected_report_format"] != _REPORT_FORMAT
    ):
        raise ValueError
    max_age = fields["max_age_seconds"]
    if type(max_age) is not int or max_age < 0:
        raise ValueError
    raw_signals = fields["expected_signals"]
    if not isinstance(raw_signals, list) or not raw_signals:
        raise ValueError
    expected_signals = tuple(_identifier(signal) for signal in raw_signals)
    if len(expected_signals) != len(set(expected_signals)):
        raise ValueError
    return _DriftGatePolicy(
        max_age_seconds=max_age,
        expected_agent_name=_identifier(fields["expected_agent_name"]),
        expected_policy_version=_identifier(fields["expected_policy_version"]),
        expected_signals=expected_signals,
    )


def _parse_signal(value: object) -> _DriftGateSignal:
    fields = _exact_fields(value, _SIGNAL_KEYS)
    status, reason = _status_reason(fields)
    baseline_count = fields["baseline_count"]
    recent_count = fields["recent_count"]
    if type(baseline_count) is not int or baseline_count <= 0:
        raise ValueError
    if type(recent_count) is not int or recent_count <= 0:
        raise ValueError
    return _DriftGateSignal(
        _identifier(fields["name"]), status, reason, baseline_count, recent_count
    )


def verify_drift_gate_report(
    report_path: Path,
    policy_path: Path,
    *,
    clock: Callable[[], datetime],
) -> DriftGateResult:
    """Check strict schema, provenance, and freshness of compact drift evidence."""
    try:
        policy = _parse_policy(policy_path.read_text(encoding="utf-8"))
        fields = _exact_fields(
            json.loads(report_path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object),
            _REPORT_KEYS,
        )
        if fields["format_version"] != _REPORT_FORMAT:
            raise ValueError
        status, reason = _status_reason(fields)
        agent_name = _identifier(fields["agent_name"])
        policy_version = _identifier(fields["policy_version"])
        if (
            agent_name != policy.expected_agent_name
            or policy_version != policy.expected_policy_version
        ):
            raise ValueError
        policy_hash = _string(fields["policy_hash"])
        if _POLICY_HASH.fullmatch(policy_hash) is None:
            raise ValueError
        baseline_id = fields["baseline_id"]
        if baseline_id is not None or status != "insufficient_data":
            baseline_id = _identifier(baseline_id)

        raw_signals = fields["signals"]
        if not isinstance(raw_signals, list) or len(raw_signals) != len(policy.expected_signals):
            raise ValueError
        signals = tuple(_parse_signal(signal) for signal in raw_signals)
        if tuple(signal.name for signal in signals) != policy.expected_signals:
            raise ValueError

        timestamp = _string(fields["generated_at"])
        if _TIMESTAMP.fullmatch(timestamp) is None:
            raise ValueError
        generated_at = datetime.fromisoformat(timestamp)
        now = clock()
        if not isinstance(now, datetime) or now.utcoffset() != timedelta(0):
            raise ValueError
        age = (now - generated_at).total_seconds()
        if age < 0 or age > policy.max_age_seconds:
            raise ValueError
        return DriftGateResult(
            format_version=_REPORT_FORMAT,
            status=status,
            reason=reason,
            generated_at=timestamp,
            agent_name=agent_name,
            policy_version=policy_version,
            policy_hash=policy_hash,
            baseline_id=baseline_id,
            signals=signals,
            age_seconds=int(age),
        )
    except (OSError, TypeError, ValueError, RecursionError):
        raise DriftGateError("Unable to verify drift report") from None
