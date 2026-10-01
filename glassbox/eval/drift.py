"""Validated drift policy and deterministic, in-memory drift calculations."""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import tomllib
from bisect import bisect_right
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean, stdev
from typing import Callable, Literal, Mapping, Sequence, TypeAlias, cast

from glassbox.store import Database, Repository
from glassbox.store.repository import (
    DriftBaselineInsert,
    DriftBaselineRecord,
    DriftDecisionSample,
    DriftResultInsert,
    DriftRunInsert,
    DriftTraceSample,
)

SignalName: TypeAlias = Literal["confidence", "decision_type", "trace_latency_ms", "trace_cost_usd"]
DriftStatus: TypeAlias = Literal["healthy", "watch", "drift_detected", "insufficient_data"]
InsufficientReason: TypeAlias = Literal[
    "baseline_not_created",
    "policy_changed_requires_rebaseline",
    "baseline_window_too_small",
    "recent_window_too_small",
]
SIGNAL_NAMES: tuple[SignalName, ...] = (
    "confidence",
    "decision_type",
    "trace_latency_ms",
    "trace_cost_usd",
)
_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _new_drift_ulid(timestamp: datetime) -> str:
    """Mint a canonical ULID using the run's captured UTC instant."""
    value = (int(timestamp.timestamp() * 1_000) << 80) | secrets.randbits(80)
    encoded = ""
    for _ in range(26):
        value, remainder = divmod(value, 32)
        encoded = _ULID_ALPHABET[remainder] + encoded
    return encoded


class DriftPolicyError(ValueError):
    """The drift policy is incomplete or invalid."""


@dataclass(frozen=True)
class SignalPolicy:
    name: SignalName
    population: Literal["decision", "trace"]
    algorithm: Literal["psi", "cusum"]
    minimum_baseline_samples: int
    minimum_recent_samples: int
    warning_threshold: float
    alert_threshold: float
    bin_boundaries: tuple[float, ...] = ()
    smoothing: float | None = None
    reference_value: float | None = None


@dataclass(frozen=True)
class DriftPolicy:
    policy_version: str
    baseline_start: datetime
    baseline_end: datetime
    recent_duration: timedelta
    signals: tuple[SignalPolicy, ...]
    policy_hash: str

    def signal(self, name: SignalName) -> SignalPolicy:
        for signal in self.signals:
            if signal.name == name:
                return signal
        raise KeyError(name)


@dataclass(frozen=True)
class DecisionSample:
    agent_name: str
    agent_version: str
    confidence: float
    decision_type: str
    decided_at: datetime


@dataclass(frozen=True)
class TraceSample:
    agent_name: str
    agent_version: str
    latency_ms: float | None
    total_cost_usd: float | None
    started_at: datetime
    decision_count: int


@dataclass(frozen=True)
class BaselineSignal:
    name: SignalName
    count: int
    labels: tuple[str, ...] = ()
    counts: tuple[int, ...] = ()
    proportions: tuple[float, ...] = ()
    mean: float | None = None
    standard_deviation: float | None = None


@dataclass(frozen=True)
class BaselineMaterial:
    policy_version: str
    policy_hash: str
    baseline_start: datetime
    baseline_end: datetime
    agent_name: str | None
    baseline_id: str | None
    signals: tuple[BaselineSignal, ...]
    version_counts: tuple[tuple[str, int, int], ...]
    decisions_per_trace: float | None

    def signal(self, name: SignalName) -> BaselineSignal:
        for signal in self.signals:
            if signal.name == name:
                return signal
        raise KeyError(name)


@dataclass(frozen=True)
class DriftSignalReport:
    name: SignalName
    population: Literal["decision", "trace"]
    algorithm: Literal["psi", "cusum"]
    baseline_count: int
    recent_count: int
    value: float | None
    warning_threshold: float
    alert_threshold: float
    status: DriftStatus
    reason: InsufficientReason | None
    details: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "population": self.population,
            "algorithm": self.algorithm,
            "baseline_count": self.baseline_count,
            "recent_count": self.recent_count,
            "value": self.value,
            "warning_threshold": self.warning_threshold,
            "alert_threshold": self.alert_threshold,
            "status": self.status,
            "reason": self.reason,
            "details": self.details.copy(),
        }


@dataclass(frozen=True)
class DriftReport:
    status: DriftStatus
    reason: InsufficientReason | None
    as_of: datetime
    baseline_start: datetime
    baseline_end: datetime
    recent_start: datetime
    recent_end: datetime
    policy_version: str
    policy_hash: str
    agent_name: str | None
    baseline_id: str | None
    baseline_versions: tuple[tuple[str, int, int], ...]
    recent_versions: tuple[tuple[str, int, int], ...]
    baseline_decisions_per_trace: float | None
    recent_decisions_per_trace: float | None
    signals: tuple[DriftSignalReport, ...]

    def signal(self, name: SignalName) -> DriftSignalReport:
        for signal in self.signals:
            if signal.name == name:
                return signal
        raise KeyError(name)

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "as_of": self.as_of.isoformat(),
            "baseline_window": {
                "start": self.baseline_start.isoformat(),
                "end": self.baseline_end.isoformat(),
            },
            "recent_window": {
                "start": self.recent_start.isoformat(),
                "end": self.recent_end.isoformat(),
            },
            "policy_version": self.policy_version,
            "policy_hash": self.policy_hash,
            "agent_name": self.agent_name,
            "baseline_id": self.baseline_id,
            "baseline_versions": _version_map(self.baseline_versions),
            "recent_versions": _version_map(self.recent_versions),
            "baseline_decisions_per_trace": self.baseline_decisions_per_trace,
            "recent_decisions_per_trace": self.recent_decisions_per_trace,
            "signals": {signal.name: signal.to_dict() for signal in self.signals},
        }


def _version_map(counts: tuple[tuple[str, int, int], ...]) -> dict[str, dict[str, int]]:
    return {
        version: {"decisions": decisions, "traces": traces} for version, decisions, traces in counts
    }


def _number(value: object, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DriftPolicyError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0) or result < 0:
        raise DriftPolicyError(f"{field} must be a finite non-negative number")
    return result


def _count(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise DriftPolicyError(f"{field} must be a positive integer")
    return value


def _timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
        raise DriftPolicyError(f"{field} must be a UTC timestamp ending in Z")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError as exc:
        raise DriftPolicyError(f"{field} is invalid") from exc


def _keys(mapping: dict[str, object], expected: set[str], field: str) -> None:
    if set(mapping) != expected:
        missing = expected - set(mapping)
        unknown = set(mapping) - expected
        raise DriftPolicyError(f"{field} fields differ: missing={missing}, unknown={unknown}")


def load_policy(path: Path) -> DriftPolicy:
    """Parse and validate the complete TOML policy before any data access."""
    try:
        mapping = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise DriftPolicyError(f"cannot read drift policy: {exc}") from exc
    _keys(
        mapping,
        {"policy_version", "baseline_start", "baseline_end", "recent_duration", *SIGNAL_NAMES},
        "policy",
    )
    version = mapping["policy_version"]
    if not isinstance(version, str) or not version.strip():
        raise DriftPolicyError("policy_version must be nonempty")
    start = _timestamp(mapping["baseline_start"], "baseline_start")
    end = _timestamp(mapping["baseline_end"], "baseline_end")
    if end <= start:
        raise DriftPolicyError("baseline_end must be after baseline_start")
    duration_text = mapping["recent_duration"]
    if not isinstance(duration_text, str) or not re.fullmatch(r"[1-9]\d*d", duration_text):
        raise DriftPolicyError("recent_duration must be positive whole days, e.g. 7d")
    try:
        duration = timedelta(days=int(duration_text[:-1]))
    except (OverflowError, ValueError) as exc:
        raise DriftPolicyError("recent_duration is too large") from exc
    signals: list[SignalPolicy] = []
    for name in SIGNAL_NAMES:
        raw = mapping[name]
        if not isinstance(raw, dict):
            raise DriftPolicyError(f"{name} must be a table")
        extras = (
            {"bin_boundaries"}
            if name == "confidence"
            else {"smoothing"}
            if name == "decision_type"
            else {"reference_value"}
        )
        _keys(
            raw,
            {
                "population",
                "algorithm",
                "minimum_baseline_samples",
                "minimum_recent_samples",
                "warning_threshold",
                "alert_threshold",
                *extras,
            },
            name,
        )
        population: Literal["decision", "trace"] = (
            "decision" if name in ("confidence", "decision_type") else "trace"
        )
        algorithm: Literal["psi", "cusum"] = "psi" if population == "decision" else "cusum"
        if raw["population"] != population or raw["algorithm"] != algorithm:
            raise DriftPolicyError(f"{name} has invalid population or algorithm")
        warning = _number(raw["warning_threshold"], f"{name}.warning_threshold")
        alert = _number(raw["alert_threshold"], f"{name}.alert_threshold", positive=True)
        if warning >= alert:
            raise DriftPolicyError(f"{name} warning threshold must be below alert threshold")
        bounds: tuple[float, ...] = ()
        smoothing: float | None = None
        reference: float | None = None
        if name == "confidence":
            raw_bounds = raw["bin_boundaries"]
            if not isinstance(raw_bounds, list) or len(raw_bounds) < 2:
                raise DriftPolicyError("confidence.bin_boundaries must contain at least two values")
            bounds = tuple(_number(item, "confidence.bin_boundaries") for item in raw_bounds)
            if (
                bounds[0] != 0.0
                or bounds[-1] != 1.0
                or any(a >= b for a, b in zip(bounds, bounds[1:]))
            ):
                raise DriftPolicyError("confidence.bin_boundaries must increase from 0.0 to 1.0")
        elif name == "decision_type":
            smoothing = _number(raw["smoothing"], "decision_type.smoothing", positive=True)
        else:
            reference = _number(raw["reference_value"], f"{name}.reference_value")
        signals.append(
            SignalPolicy(
                name,
                population,
                algorithm,
                _count(raw["minimum_baseline_samples"], f"{name}.minimum_baseline_samples"),
                _count(raw["minimum_recent_samples"], f"{name}.minimum_recent_samples"),
                warning,
                alert,
                bounds,
                smoothing,
                reference,
            )
        )
    canonical = json.dumps(mapping, sort_keys=True, separators=(",", ":"), allow_nan=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return DriftPolicy(version, start, end, duration, tuple(signals), digest)


def policy_hash(policy: DriftPolicy) -> str:
    """Return the canonical hash captured when the policy was validated."""
    return policy.policy_hash


def default_policy_path() -> Path:
    """Return the checked-in drift policy location."""
    return Path(__file__).parent / "policies" / "drift_v1.toml"


def _confidence_bin(value: float, bounds: tuple[float, ...]) -> int:
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("confidence must be finite and between 0 and 1")
    return min(bisect_right(bounds, value) - 1, len(bounds) - 2)


def _versions(
    decisions: Sequence[DecisionSample], traces: Sequence[TraceSample]
) -> tuple[tuple[str, int, int], ...]:
    counts: dict[str, list[int]] = {}
    for decision in decisions:
        counts.setdefault(decision.agent_version, [0, 0])[0] += 1
    for trace in traces:
        counts.setdefault(trace.agent_version, [0, 0])[1] += 1
    return tuple((version, counts[version][0], counts[version][1]) for version in sorted(counts))


def _decisions_per_trace(traces: Sequence[TraceSample]) -> float | None:
    return sum(trace.decision_count for trace in traces) / len(traces) if traces else None


def calculate_baseline(
    policy: DriftPolicy, decisions: Sequence[DecisionSample], traces: Sequence[TraceSample]
) -> BaselineMaterial:
    """Materialize distributions and statistics without retaining source events."""
    bounds = policy.signal("confidence").bin_boundaries
    confidence_counts = [0] * (len(bounds) - 1)
    kind_counts: dict[str, int] = {}
    for decision in decisions:
        confidence_counts[_confidence_bin(decision.confidence, bounds)] += 1
        kind_counts[decision.decision_type] = kind_counts.get(decision.decision_type, 0) + 1
    confidence_labels = tuple(
        f"[{a}, {b}{']' if i == len(bounds) - 2 else ')'}"
        for i, (a, b) in enumerate(zip(bounds, bounds[1:]))
    )
    kind_labels = tuple(sorted(kind for kind in kind_counts if kind != "other")) + ("other",)
    kind_values = tuple(kind_counts.get(kind, 0) for kind in kind_labels)
    latency = [trace.latency_ms for trace in traces if trace.latency_ms is not None]
    cost = [trace.total_cost_usd for trace in traces if trace.total_cost_usd is not None]

    def distribution(
        name: SignalName, labels: tuple[str, ...], counts: tuple[int, ...]
    ) -> BaselineSignal:
        total = sum(counts)
        return BaselineSignal(
            name,
            total,
            labels,
            counts,
            tuple(count / total for count in counts) if total else tuple(0.0 for _ in counts),
        )

    def trace_stat(name: SignalName, values: list[float]) -> BaselineSignal:
        if any(not math.isfinite(value) for value in values):
            raise ValueError(f"{name} samples must be finite")
        average = mean(values) if values else None
        deviation = stdev(values) if len(values) >= 2 else None
        if (average is not None and not math.isfinite(average)) or (
            deviation is not None and not math.isfinite(deviation)
        ):
            raise ValueError(f"{name} baseline statistics must be finite")
        return BaselineSignal(
            name,
            len(values),
            mean=average,
            standard_deviation=deviation,
        )

    signals = (
        distribution("confidence", confidence_labels, tuple(confidence_counts)),
        distribution("decision_type", kind_labels, kind_values),
        trace_stat("trace_latency_ms", latency),
        trace_stat("trace_cost_usd", cost),
    )
    agent_name = next(
        (sample.agent_name for sample in decisions),
        next((sample.agent_name for sample in traces), None),
    )
    return BaselineMaterial(
        policy.policy_version,
        policy.policy_hash,
        policy.baseline_start,
        policy.baseline_end,
        agent_name,
        None,
        signals,
        _versions(decisions, traces),
        _decisions_per_trace(traces),
    )


def _psi(baseline: tuple[int, ...], recent: tuple[int, ...], smoothing: float) -> float:
    def log_smoothed(count: int) -> float:
        larger = max(count, smoothing)
        smaller = min(count, smoothing)
        return math.log(larger) + math.log1p(smaller / larger)

    def log_total(log_counts: tuple[float, ...]) -> float:
        largest = max(log_counts)
        return largest + math.log(math.fsum(math.exp(value - largest) for value in log_counts))

    baseline_logs = tuple(log_smoothed(count) for count in baseline)
    recent_logs = tuple(log_smoothed(count) for count in recent)
    baseline_total = log_total(baseline_logs)
    recent_total = log_total(recent_logs)
    return math.fsum(
        (math.exp(r - recent_total) - math.exp(b - baseline_total))
        * ((r - recent_total) - (b - baseline_total))
        for b, r in zip(baseline_logs, recent_logs)
    )


def _cusum(values: Sequence[float], center: float, deviation: float, reference: float) -> float:
    positive = negative = 0.0
    for value in values:
        residual = (value - center) / deviation
        positive = max(0.0, positive + residual - reference)
        negative = min(0.0, negative + residual + reference)
    return max(positive, abs(negative))


def _status(value: float, policy: SignalPolicy) -> DriftStatus:
    if value >= policy.alert_threshold:
        return "drift_detected"
    if value >= policy.warning_threshold:
        return "watch"
    return "healthy"


def calculate_report(
    policy: DriftPolicy,
    baseline: BaselineMaterial,
    recent_decisions: Sequence[DecisionSample],
    recent_traces: Sequence[TraceSample],
    as_of: datetime,
) -> DriftReport:
    """Calculate the four signals against immutable materialized baseline values."""
    if as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
        raise ValueError("as_of must be UTC")
    if baseline.policy_hash != policy.policy_hash:
        raise ValueError("baseline policy hash differs from policy")
    reports: list[DriftSignalReport] = []
    for name in SIGNAL_NAMES:
        rule = policy.signal(name)
        old = baseline.signal(name)
        if name == "confidence":
            counts = [0] * len(old.counts)
            for sample in recent_decisions:
                counts[_confidence_bin(sample.confidence, rule.bin_boundaries)] += 1
            current_counts = tuple(counts)
            values: list[float] = []
            details: dict[str, object] = {
                "bins": list(old.labels),
                "baseline_counts": list(old.counts),
                "recent_counts": list(current_counts),
            }
        elif name == "decision_type":
            categories = set(old.labels)
            category_counts = dict.fromkeys(old.labels, 0)
            for sample in recent_decisions:
                category = sample.decision_type if sample.decision_type in categories else "other"
                category_counts[category] += 1
            current_counts = tuple(category_counts[label] for label in old.labels)
            values = []
            details = {
                "categories": list(old.labels),
                "baseline_counts": list(old.counts),
                "recent_counts": list(current_counts),
            }
        else:
            values = [
                value
                for trace in recent_traces
                if (
                    value := trace.latency_ms
                    if name == "trace_latency_ms"
                    else trace.total_cost_usd
                )
                is not None
            ]
            if any(not math.isfinite(value) for value in values):
                raise ValueError(f"{name} samples must be finite")
            current_counts = ()
            details = {
                "baseline_mean": old.mean,
                "baseline_standard_deviation": old.standard_deviation,
                "reference_value": rule.reference_value,
            }
        recent_count = (
            len(recent_decisions) if name in ("confidence", "decision_type") else len(values)
        )
        reason: InsufficientReason | None = None
        metric: float | None = None
        if old.count < rule.minimum_baseline_samples or (
            rule.algorithm == "cusum"
            and (old.standard_deviation is None or old.standard_deviation == 0)
        ):
            reason = "baseline_window_too_small"
        elif recent_count < rule.minimum_recent_samples:
            reason = "recent_window_too_small"
        elif rule.algorithm == "psi":
            metric = _psi(
                old.counts, current_counts, rule.smoothing if rule.smoothing is not None else 0.0001
            )
        else:
            assert (
                old.mean is not None
                and old.standard_deviation is not None
                and rule.reference_value is not None
            )
            metric = _cusum(values, old.mean, old.standard_deviation, rule.reference_value)
        if metric is not None and not math.isfinite(metric):
            raise ValueError(f"{name} produced a non-finite drift metric")
        status: DriftStatus = (
            "insufficient_data"
            if reason
            else _status(metric, rule)
            if metric is not None
            else "insufficient_data"
        )
        reports.append(
            DriftSignalReport(
                name,
                rule.population,
                rule.algorithm,
                old.count,
                recent_count,
                metric,
                rule.warning_threshold,
                rule.alert_threshold,
                status,
                reason,
                details,
            )
        )
    reasons = {report.reason for report in reports}
    aggregate_reason: InsufficientReason | None = None
    if "baseline_window_too_small" in reasons:
        aggregate_reason = "baseline_window_too_small"
    elif "recent_window_too_small" in reasons:
        aggregate_reason = "recent_window_too_small"
    if aggregate_reason:
        aggregate_status: DriftStatus = "insufficient_data"
    elif any(report.status == "drift_detected" for report in reports):
        aggregate_status = "drift_detected"
    elif any(report.status == "watch" for report in reports):
        aggregate_status = "watch"
    else:
        aggregate_status = "healthy"
    agent_name = baseline.agent_name or next(
        (sample.agent_name for sample in recent_decisions),
        next((sample.agent_name for sample in recent_traces), None),
    )
    return DriftReport(
        aggregate_status,
        aggregate_reason,
        as_of,
        baseline.baseline_start,
        baseline.baseline_end,
        as_of - policy.recent_duration,
        as_of,
        policy.policy_version,
        policy.policy_hash,
        agent_name,
        baseline.baseline_id,
        baseline.version_counts,
        _versions(recent_decisions, recent_traces),
        baseline.decisions_per_trace,
        _decisions_per_trace(recent_traces),
        tuple(reports),
    )


def _source_decisions(source: Sequence[DriftDecisionSample]) -> tuple[DecisionSample, ...]:
    return tuple(
        DecisionSample(
            sample.agent_name,
            sample.agent_version,
            sample.confidence,
            sample.decision_type,
            sample.decided_at,
        )
        for sample in source
    )


def _source_traces(source: Sequence[DriftTraceSample]) -> tuple[TraceSample, ...]:
    return tuple(
        TraceSample(
            sample.agent_name,
            sample.agent_version,
            sample.latency_ms,
            sample.total_cost_usd,
            sample.started_at,
            sample.decision_count,
        )
        for sample in source
    )


def _stored_mapping(value: object, fields: set[str], name: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise DriftPolicyError(f"stored drift baseline has invalid {name}")
    return value


def _stored_count(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DriftPolicyError(f"stored drift baseline has invalid {name}")
    return value


def _stored_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise DriftPolicyError(f"stored drift baseline has invalid {name}")
    try:
        number = float(value)
    except OverflowError as exc:
        raise DriftPolicyError(f"stored drift baseline has invalid {name}") from exc
    if not math.isfinite(number):
        raise DriftPolicyError(f"stored drift baseline has invalid {name}")
    return number


def _material_from_record(record: DriftBaselineRecord, policy: DriftPolicy) -> BaselineMaterial:
    reference = _stored_mapping(record.reference, set(SIGNAL_NAMES), "reference")
    signal_fields = {
        "name",
        "count",
        "labels",
        "counts",
        "proportions",
        "mean",
        "standard_deviation",
    }

    def signal_from_reference(name: SignalName) -> BaselineSignal:
        value = _stored_mapping(reference[name], signal_fields, f"{name} reference")
        if value["name"] != name:
            raise DriftPolicyError(f"stored drift baseline has invalid {name} name")
        count = _stored_count(value["count"], f"{name} count")
        if count < policy.signal(name).minimum_baseline_samples:
            raise DriftPolicyError(f"stored drift baseline has invalid {name} count")
        labels_raw, counts_raw, proportions_raw = (
            value["labels"],
            value["counts"],
            value["proportions"],
        )
        if not all(isinstance(item, list) for item in (labels_raw, counts_raw, proportions_raw)):
            raise DriftPolicyError(f"stored drift baseline has invalid {name} arrays")
        labels_list = cast(list[object], labels_raw)
        counts_list = cast(list[object], counts_raw)
        proportions_list = cast(list[object], proportions_raw)
        if name in ("confidence", "decision_type"):
            if (
                not labels_list
                or len(labels_list) != len(counts_list)
                or len(labels_list) != len(proportions_list)
                or any(not isinstance(label, str) or not label for label in labels_list)
                or len(set(labels_list)) != len(labels_list)
            ):
                raise DriftPolicyError(f"stored drift baseline has invalid {name} histogram")
            labels = cast(tuple[str, ...], tuple(labels_list))
            if name == "confidence":
                bounds = policy.signal(name).bin_boundaries
                expected = tuple(
                    f"[{a}, {b}{']' if i == len(bounds) - 2 else ')'}"
                    for i, (a, b) in enumerate(zip(bounds, bounds[1:]))
                )
                if labels != expected:
                    raise DriftPolicyError("stored drift baseline has invalid confidence bins")
            elif labels != tuple(sorted(labels[:-1])) + ("other",):
                raise DriftPolicyError("stored drift baseline has invalid decision_type labels")
            counts = tuple(_stored_count(item, f"{name} histogram count") for item in counts_list)
            proportions = tuple(
                _stored_number(item, f"{name} proportion") for item in proportions_list
            )
            if (
                sum(counts) != count
                or any(
                    not math.isclose(proportion, tally / count, rel_tol=1e-12, abs_tol=1e-12)
                    for tally, proportion in zip(counts, proportions, strict=True)
                )
                or value["mean"] is not None
                or value["standard_deviation"] is not None
            ):
                raise DriftPolicyError(f"stored drift baseline has invalid {name} histogram")
            return BaselineSignal(name, count, labels, counts, proportions)

        if labels_list or counts_list or proportions_list:
            raise DriftPolicyError(f"stored drift baseline has invalid {name} trace arrays")
        average = _stored_number(value["mean"], f"{name} mean")
        deviation = _stored_number(value["standard_deviation"], f"{name} deviation")
        return BaselineSignal(name, count, mean=average, standard_deviation=deviation)

    signals = tuple(signal_from_reference(name) for name in SIGNAL_NAMES)
    counts_material = _stored_mapping(
        record.version_counts, {"versions", "decisions_per_trace"}, "version counts"
    )
    raw_versions = counts_material["versions"]
    if not isinstance(raw_versions, dict):
        raise DriftPolicyError("stored drift baseline has invalid versions")
    versions_list: list[tuple[str, int, int]] = []
    for version, raw in raw_versions.items():
        if not isinstance(version, str) or not version:
            raise DriftPolicyError("stored drift baseline has invalid version name")
        entry = _stored_mapping(raw, {"decisions", "traces"}, f"{version} counts")
        versions_list.append(
            (
                version,
                _stored_count(entry["decisions"], f"{version} decisions"),
                _stored_count(entry["traces"], f"{version} traces"),
            )
        )
    versions = tuple(sorted(versions_list))
    if (
        sum(decisions for _, decisions, _ in versions) != signals[0].count
        or signals[0].count != signals[1].count
        or any(signal.count > sum(traces for _, _, traces in versions) for signal in signals[2:])
    ):
        raise DriftPolicyError("stored drift baseline has inconsistent version counts")
    decisions_per_trace = _stored_number(
        counts_material["decisions_per_trace"], "decisions per trace"
    )
    return BaselineMaterial(
        record.policy_version,
        record.policy_hash,
        record.baseline_start,
        record.baseline_end,
        record.agent_name,
        record.baseline_id,
        signals,
        versions,
        decisions_per_trace,
    )


def create_baseline(
    database_path: Path,
    agent_name: str,
    policy: DriftPolicy,
    *,
    supersede: bool,
    clock: Callable[[], datetime],
) -> DriftBaselineRecord:
    """Calculate one historical baseline and append its immutable snapshot."""
    if not database_path.is_file():
        raise FileNotFoundError(database_path)
    created_at = clock()
    database = Database.open(database_path)
    try:
        repository = Repository(database)
        source = repository.drift_source_data(
            agent_name,
            policy.baseline_start,
            policy.baseline_end,
            policy.baseline_start,
            policy.baseline_end,
        )
        material = calculate_baseline(
            policy,
            _source_decisions(source.baseline_decisions),
            _source_traces(source.baseline_traces),
        )
        for signal in material.signals:
            floor = policy.signal(signal.name).minimum_baseline_samples
            if signal.count < floor:
                raise DriftPolicyError(
                    f"{signal.name} baseline has {signal.count} samples; requires {floor}"
                )
        request = DriftBaselineInsert(
            baseline_id=_new_drift_ulid(created_at),
            agent_name=agent_name,
            policy_version=policy.policy_version,
            policy_hash=policy.policy_hash,
            baseline_start=policy.baseline_start,
            baseline_end=policy.baseline_end,
            created_at=created_at,
            version_counts={
                "versions": _version_map(material.version_counts),
                "decisions_per_trace": material.decisions_per_trace,
            },
            reference={signal.name: asdict(signal) for signal in material.signals},
        )
        return repository.record_drift_baseline(request, supersede=supersede)
    finally:
        database.close()


def _missing_baseline_report(
    policy: DriftPolicy,
    agent_name: str,
    as_of: datetime,
    reason: InsufficientReason,
) -> DriftReport:
    signals = tuple(
        DriftSignalReport(
            signal.name,
            signal.population,
            signal.algorithm,
            0,
            0,
            None,
            signal.warning_threshold,
            signal.alert_threshold,
            "insufficient_data",
            reason,
            {},
        )
        for signal in policy.signals
    )
    return DriftReport(
        "insufficient_data",
        reason,
        as_of,
        policy.baseline_start,
        policy.baseline_end,
        as_of - policy.recent_duration,
        as_of,
        policy.policy_version,
        policy.policy_hash,
        agent_name,
        None,
        (),
        (),
        None,
        None,
        signals,
    )


def run_drift_report(
    database_path: Path,
    agent_name: str,
    policy: DriftPolicy,
    *,
    clock: Callable[[], datetime],
    persist: bool,
) -> DriftReport:
    """Use one instant for querying, calculation, output, and optional persistence."""
    as_of = clock()
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() != timedelta(0):
        raise ValueError("as_of must be UTC")
    recent_start = as_of - policy.recent_duration
    database = (
        Database.open(database_path)
        if persist and database_path.is_file()
        else Database.open_read_only(database_path)
    )
    try:
        repository = Repository(database)
        state = repository.drift_baseline_state(agent_name, policy.policy_hash)
        if state.status == "malformed":
            raise DriftPolicyError("drift baseline history is malformed")
        if state.baseline is None:
            other_policy = database.connection.execute(
                "SELECT 1 FROM drift_baselines WHERE agent_name = ? AND policy_hash != ? LIMIT 1",
                (agent_name, policy.policy_hash),
            ).fetchone()
            reason: InsufficientReason = (
                "policy_changed_requires_rebaseline" if other_policy else "baseline_not_created"
            )
            return _missing_baseline_report(policy, agent_name, as_of, reason)
        baseline = _material_from_record(state.baseline, policy)
        source = repository.drift_recent_source_data(agent_name, recent_start, as_of)
        report = calculate_report(
            policy,
            baseline,
            _source_decisions(source.recent_decisions),
            _source_traces(source.recent_traces),
            as_of,
        )
        if persist:
            run = DriftRunInsert(
                drift_run_id=_new_drift_ulid(as_of),
                baseline_id=cast(str, report.baseline_id),
                agent_name=agent_name,
                policy_version=report.policy_version,
                policy_hash=report.policy_hash,
                as_of=report.as_of,
                recent_start=report.recent_start,
                recent_end=report.recent_end,
                status=report.status,
                status_reason=report.reason,
                version_counts=_version_map(report.recent_versions),
                context={
                    "baseline_versions": _version_map(report.baseline_versions),
                    "baseline_decisions_per_trace": report.baseline_decisions_per_trace,
                    "recent_decisions_per_trace": report.recent_decisions_per_trace,
                },
            )
            results = tuple(
                DriftResultInsert(
                    drift_result_id=_new_drift_ulid(as_of),
                    signal_name=signal.name,
                    population=signal.population,
                    algorithm=signal.algorithm,
                    status=signal.status,
                    baseline_count=signal.baseline_count,
                    recent_count=signal.recent_count,
                    metric_value=signal.value,
                    warning_threshold=signal.warning_threshold,
                    alert_threshold=signal.alert_threshold,
                    details=signal.details,
                )
                for signal in report.signals
            )
            repository.record_drift_run(run, results)
        return report
    finally:
        database.close()
