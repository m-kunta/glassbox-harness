"""Agreement and operational metrics for deterministic evaluation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

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


def _weight(actual: str, prediction: str) -> float:
    return abs(_INDEX[actual] - _INDEX[prediction]) / (len(_URGENCIES) - 1)


def _validate_pairs(expected: Sequence[str], predicted: Sequence[str]) -> None:
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted urgencies must have equal lengths")
    invalid = (set(expected) | set(predicted)) - set(_URGENCIES)
    if invalid:
        raise ValueError(f"unknown urgency values: {', '.join(sorted(invalid))}")
