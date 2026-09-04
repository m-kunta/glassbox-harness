"""Agreement and operational metrics for deterministic evaluation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Mapping

_URGENCIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
_INDEX = {urgency: index for index, urgency in enumerate(_URGENCIES)}


def urgency_confusion_matrix(
    expected: Sequence[str], predicted: Sequence[str]
) -> dict[str, dict[str, int]]:
    """Return a complete expected-by-predicted urgency count matrix."""
    _validate_pairs(expected, predicted)
    matrix = {actual: {prediction: 0 for prediction in _URGENCIES} for actual in _URGENCIES}
    for actual, prediction in zip(expected, predicted, strict=True):
        matrix[actual][prediction] += 1
    return matrix


def linear_weighted_kappa(expected: Sequence[str], predicted: Sequence[str]) -> float:
    """Return linear weighted Cohen's kappa for the four SLA urgency tiers."""
    _validate_pairs(expected, predicted)
    if not expected:
        return 1.0
    if len(set(expected)) == len(set(predicted)) == 1:
        return 1.0 if expected[0] == predicted[0] else 0.0

    count = len(expected)
    expected_counts = Counter(expected)
    predicted_counts = Counter(predicted)
    observed = (
        sum(
            _weight(actual, prediction)
            for actual, prediction in zip(expected, predicted, strict=True)
        )
        / count
    )
    chance = sum(
        _weight(actual, prediction)
        * expected_counts[actual]
        * predicted_counts[prediction]
        / (count * count)
        for actual in _URGENCIES
        for prediction in _URGENCIES
    )
    return 1.0 if chance == 0 else 1.0 - observed / chance


def operational_metrics(
    measurements: Sequence[Mapping[str, float | int]], *, error_count: int
) -> dict[str, float | int]:
    """Aggregate target-reported execution measurements across a suite."""
    count = len(measurements)
    latencies = sorted(float(item.get("latency_ms", 0.0)) for item in measurements)
    costs = [float(item.get("cost_usd", 0.0)) for item in measurements]
    tokens = [int(item.get("tokens", 0)) for item in measurements]
    return {
        "p50_latency_ms": _percentile(latencies, 0.5),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "total_cost_usd": sum(costs),
        "cost_per_decision": sum(costs) / count if count else 0.0,
        "total_tokens": sum(tokens),
        "tokens_per_decision": sum(tokens) / count if count else 0.0,
        "error_rate": error_count / count if count else 0.0,
    }


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    position = (len(values) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def _weight(actual: str, prediction: str) -> float:
    return abs(_INDEX[actual] - _INDEX[prediction]) / (len(_URGENCIES) - 1)


def _validate_pairs(expected: Sequence[str], predicted: Sequence[str]) -> None:
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted urgencies must have equal lengths")
    invalid = (set(expected) | set(predicted)) - set(_URGENCIES)
    if invalid:
        raise ValueError(f"unknown urgency values: {', '.join(sorted(invalid))}")
