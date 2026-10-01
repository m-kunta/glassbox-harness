"""Pure drift policy and metric contract tests."""

import json
import math
from collections.abc import Iterator, Sequence
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path

import pytest

from glassbox.eval.drift import (
    DecisionSample,
    DriftPolicyError,
    TraceSample,
    _status,
    calculate_baseline,
    calculate_report,
    load_policy,
    policy_hash,
)

POLICY_PATH = Path(__file__).resolve().parents[2] / "glassbox/eval/policies/drift_v1.toml"
BASELINE_AT = datetime(2026, 2, 1, tzinfo=UTC)
RECENT_AT = datetime(2026, 7, 30, tzinfo=UTC)
AS_OF = datetime(2026, 8, 1, tzinfo=UTC)


@pytest.fixture
def policy():  # type: ignore[no-untyped-def]
    return load_policy(POLICY_PATH)


def decisions(confidence: float, kind: str, count: int, *, recent: bool = False):  # type: ignore[no-untyped-def]
    at = RECENT_AT if recent else BASELINE_AT
    version = "v2" if recent else "v1"
    return tuple(DecisionSample("a", version, confidence, kind, at) for _ in range(count))


def traces(latency: float, cost: float, count: int, *, recent: bool = False):  # type: ignore[no-untyped-def]
    at = RECENT_AT if recent else BASELINE_AT
    version = "v2" if recent else "v1"
    return tuple(TraceSample("a", version, latency, cost, at, 2) for _ in range(count))


def test_checked_in_policy_loads_and_hash_is_canonical(tmp_path: Path) -> None:
    original = POLICY_PATH.read_text(encoding="utf-8")
    alternate = tmp_path / "alternate.toml"
    alternate.write_text(
        "\n" + original.replace('policy_version = "drift_v1"', 'policy_version = "drift_v1"  ')
    )
    first = load_policy(POLICY_PATH)
    second = load_policy(alternate)
    assert first.policy_version == "drift_v1"
    assert first.policy_hash == second.policy_hash == policy_hash(first)
    assert len(first.policy_hash) == 64


@pytest.mark.parametrize(
    "old,new",
    [
        ('policy_version = "drift_v1"', 'policy_version = ""'),
        ('recent_duration = "7d"', 'recent_duration = "0d"'),
        ('baseline_start = "2026-01-01T00:00:00Z"', 'baseline_start = "2026-01-01"'),
        ('baseline_end = "2026-06-01T00:00:00Z"', 'baseline_end = "2025-01-01T00:00:00Z"'),
        ("0.0, 0.1, 0.2", "0.0, 0.2, 0.1"),
        ("0.9, 1.0]", "0.9, 0.9]"),
        ("0.0, 0.1, 0.2", "0.01, 0.1, 0.2"),
        ("minimum_recent_samples = 30", "minimum_recent_samples = 0"),
        ("smoothing = 0.0001", "smoothing = 0.0"),
        ("alert_threshold = 0.20", "alert_threshold = 0.05"),
        ("reference_value = 0.5", "reference_value = nan"),
        ("[trace_cost_usd]", "[unknown_signal]"),
    ],
)
def test_policy_rejects_invalid_input(tmp_path: Path, old: str, new: str) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(POLICY_PATH.read_text().replace(old, new), encoding="utf-8")
    with pytest.raises(DriftPolicyError):
        load_policy(path)


def test_confidence_and_decision_type_psi_detect_shift_without_mutating_baseline(policy) -> None:  # type: ignore[no-untyped-def]
    baseline = calculate_baseline(policy, decisions(0.05, "review", 100), ())
    before = repr(baseline)
    recent = decisions(0.95, "order", 30, recent=True)
    report = calculate_report(policy, baseline, recent, (), AS_OF)
    assert report.signal("confidence").status == "drift_detected"
    assert report.signal("decision_type").status == "drift_detected"
    assert repr(baseline) == before


def test_confidence_bins_include_lower_edges_and_one(policy) -> None:  # type: ignore[no-untyped-def]
    samples = (
        DecisionSample("a", "v1", 0.0, "review", BASELINE_AT),
        DecisionSample("a", "v1", 0.1, "review", BASELINE_AT),
        DecisionSample("a", "v1", 1.0, "review", BASELINE_AT),
    )
    baseline = calculate_baseline(policy, samples, ())
    assert baseline.signal("confidence").counts == (1, 1, 0, 0, 0, 0, 0, 0, 0, 1)
    assert baseline.signal("confidence").labels[0] == "[0.0, 0.1)"
    assert baseline.signal("confidence").labels[-1] == "[0.9, 1.0]"


def test_decision_type_uses_other_with_smoothing_for_absent_categories(policy) -> None:  # type: ignore[no-untyped-def]
    baseline = calculate_baseline(policy, decisions(0.5, "review", 100), ())
    report = calculate_report(
        policy, baseline, decisions(0.5, "new_kind", 30, recent=True), (), AS_OF
    )
    signal = report.signal("decision_type")
    assert signal.value is not None and signal.value > 0
    assert signal.details["categories"] == ["review", "other"]
    assert signal.details["recent_counts"] == [0, 30]


def test_cusum_detects_positive_and_negative_trace_shifts(policy) -> None:  # type: ignore[no-untyped-def]
    baseline_samples = tuple(
        TraceSample("a", "v1", 9.0 if i % 2 else 11.0, 0.9 if i % 2 else 1.1, BASELINE_AT, 1)
        for i in range(50)
    )
    baseline = calculate_baseline(policy, (), baseline_samples)
    high = traces(30.0, 4.0, 20, recent=True)
    low = traces(1.0, 0.1, 20, recent=True)
    assert (
        calculate_report(policy, baseline, (), high, AS_OF).signal("trace_latency_ms").status
        == "drift_detected"
    )
    assert (
        calculate_report(policy, baseline, (), low, AS_OF).signal("trace_cost_usd").status
        == "drift_detected"
    )


def test_zero_cusum_variance_is_insufficient_baseline(policy) -> None:  # type: ignore[no-untyped-def]
    baseline = calculate_baseline(policy, (), traces(10.0, 1.0, 50))
    report = calculate_report(policy, baseline, (), traces(30.0, 4.0, 20, recent=True), AS_OF)
    assert report.signal("trace_latency_ms").status == "insufficient_data"
    assert report.signal("trace_latency_ms").reason == "baseline_window_too_small"
    assert report.status == "insufficient_data"
    assert report.reason == "baseline_window_too_small"


@pytest.mark.parametrize(
    "baseline_decisions,baseline_traces,recent_decisions,recent_traces,reason",
    [
        (99, 50, 30, 20, "baseline_window_too_small"),
        (100, 49, 30, 20, "baseline_window_too_small"),
        (100, 50, 29, 20, "recent_window_too_small"),
        (100, 50, 30, 19, "recent_window_too_small"),
        (99, 50, 29, 20, "baseline_window_too_small"),
    ],
)
def test_insufficient_data_aggregation_precedence(
    policy, baseline_decisions, baseline_traces, recent_decisions, recent_traces, reason
) -> None:  # type: ignore[no-untyped-def]
    varied_baseline_traces = tuple(
        TraceSample("a", "v1", 9.0 if i % 2 else 11.0, 0.9 if i % 2 else 1.1, BASELINE_AT, 1)
        for i in range(baseline_traces)
    )
    baseline = calculate_baseline(
        policy, decisions(0.5, "review", baseline_decisions), varied_baseline_traces
    )
    report = calculate_report(
        policy,
        baseline,
        decisions(0.5, "review", recent_decisions, recent=True),
        traces(10.0, 1.0, recent_traces, recent=True),
        AS_OF,
    )
    assert report.status == "insufficient_data"
    assert report.reason == reason


def test_threshold_boundaries_and_watch(tmp_path: Path) -> None:
    text = POLICY_PATH.read_text()
    text = text.replace("warning_threshold = 0.10", "warning_threshold = 0.0")
    text = text.replace("alert_threshold = 0.20", "alert_threshold = 1.0")
    path = tmp_path / "thresholds.toml"
    path.write_text(text)
    policy = load_policy(path)
    varied = tuple(
        TraceSample("a", "v1", 9.0 if i % 2 else 11.0, 0.9 if i % 2 else 1.1, BASELINE_AT, 1)
        for i in range(50)
    )
    baseline = calculate_baseline(policy, decisions(0.5, "review", 100), varied)
    same = calculate_report(
        policy,
        baseline,
        decisions(0.5, "review", 30, recent=True),
        traces(10.0, 1.0, 20, recent=True),
        AS_OF,
    )
    assert same.signal("confidence").status == "watch"  # exactly warning boundary
    assert same.status == "watch"

    path.write_text(text.replace("alert_threshold = 1.0", "alert_threshold = 0.0"))
    with pytest.raises(DriftPolicyError):
        load_policy(path)


def test_exact_warning_and_alert_boundaries(policy) -> None:  # type: ignore[no-untyped-def]
    signal = policy.signal("confidence")
    assert _status(signal.warning_threshold, signal) == "watch"
    assert _status(signal.alert_threshold, signal) == "drift_detected"


def test_diagnostics_are_immutable_and_json_safe(policy) -> None:  # type: ignore[no-untyped-def]
    baseline = calculate_baseline(policy, decisions(0.5, "review", 100), traces(10.0, 1.0, 50))
    report = calculate_report(
        policy,
        baseline,
        decisions(0.5, "review", 30, recent=True),
        traces(10.0, 1.0, 20, recent=True),
        AS_OF,
    )
    payload = report.to_dict()
    assert payload["baseline_versions"] == {"v1": {"decisions": 100, "traces": 50}}
    assert payload["recent_versions"] == {"v2": {"decisions": 30, "traces": 20}}
    assert payload["baseline_decisions_per_trace"] == 2.0
    assert payload["recent_decisions_per_trace"] == 2.0
    assert len(payload["signals"]) == 4
    assert payload["as_of"] == AS_OF.isoformat()
    with pytest.raises((FrozenInstanceError, AttributeError)):
        report.status = "healthy"
    with pytest.raises(KeyError):
        report.signal("unknown")


def test_large_finite_smoothing_keeps_psi_and_json_finite(tmp_path: Path) -> None:
    path = tmp_path / "large-smoothing.toml"
    path.write_text(
        POLICY_PATH.read_text().replace("smoothing = 0.0001", "smoothing = 1e308"),
        encoding="utf-8",
    )
    policy = load_policy(path)
    baseline = calculate_baseline(policy, decisions(0.5, "known", 100), ())
    report = calculate_report(
        policy, baseline, decisions(0.5, "unknown", 30, recent=True), (), AS_OF
    )
    metric = report.signal("decision_type").value
    assert metric is not None and math.isfinite(metric)
    json.dumps(report.to_dict(), allow_nan=False)


def test_nonfinite_cusum_metric_is_rejected_before_serialization(policy) -> None:  # type: ignore[no-untyped-def]
    baseline_traces = tuple(
        TraceSample("a", "v1", 0.0 if i % 2 else 2e-100, 0.9 if i % 2 else 1.1, BASELINE_AT, 1)
        for i in range(50)
    )
    baseline = calculate_baseline(policy, (), baseline_traces)
    with pytest.raises(ValueError, match="non-finite"):
        calculate_report(policy, baseline, (), traces(1e308, 1.0, 20, recent=True), AS_OF)


def test_oversized_recent_duration_is_policy_error(tmp_path: Path) -> None:
    path = tmp_path / "huge-duration.toml"
    path.write_text(
        POLICY_PATH.read_text().replace(
            'recent_duration = "7d"', 'recent_duration = "1000000000d"'
        ),
        encoding="utf-8",
    )
    with pytest.raises(DriftPolicyError, match="recent_duration"):
        load_policy(path)


def test_decision_categories_count_recent_samples_once(policy) -> None:  # type: ignore[no-untyped-def]
    class CountedSequence(Sequence[DecisionSample]):
        def __init__(self, samples: tuple[DecisionSample, ...]) -> None:
            self.samples = samples
            self.iterations = 0

        def __len__(self) -> int:
            return len(self.samples)

        def __getitem__(self, index: int) -> DecisionSample:
            return self.samples[index]

        def __iter__(self) -> Iterator[DecisionSample]:
            self.iterations += 1
            yield from self.samples

    baseline_decisions = tuple(
        DecisionSample("a", "v1", 0.5, f"kind-{i % 8}", BASELINE_AT) for i in range(100)
    )
    recent = CountedSequence(decisions(0.5, "unseen", 30, recent=True))
    baseline = calculate_baseline(policy, baseline_decisions, ())
    report = calculate_report(policy, baseline, recent, (), AS_OF)
    assert report.signal("decision_type").details["recent_counts"] == [0] * 8 + [30]
    assert recent.iterations <= 3  # confidence, decision type, and version diagnostics
