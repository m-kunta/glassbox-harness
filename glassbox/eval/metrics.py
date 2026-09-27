"""Agreement and operational metrics for deterministic evaluation."""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Mapping, TypeVar

_URGENCIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
_INDEX = {urgency: index for index, urgency in enumerate(_URGENCIES)}

_ORDINAL_SCORES = (1, 2, 3, 4, 5)
_ORDINAL_INDEX = {score: index for index, score in enumerate(_ORDINAL_SCORES)}
_ORDINAL_SCORE_SET = set(_ORDINAL_SCORES)

_BOOTSTRAP_SEED = 0
_DEFAULT_BOOTSTRAP_SAMPLES = 1_000
_BOOTSTRAP_LOW_PERCENTILE = 0.025
_BOOTSTRAP_HIGH_PERCENTILE = 0.975

_T = TypeVar("_T")


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
    """Return linear weighted Cohen's kappa for the four SLA urgency tiers.

    Degenerate cases are a deliberate P1 design choice and must not change:
    an empty pair of sequences returns ``1.0``, and inputs where both sides
    use exactly one (possibly different) label return ``1.0`` for a match or
    ``0.0`` for a mismatch, rather than an undefined ratio.
    """
    _validate_pairs(expected, predicted)
    if not expected:
        return 1.0
    if len(set(expected)) == len(set(predicted)) == 1:
        return 1.0 if expected[0] == predicted[0] else 0.0

    return _weighted_kappa_core(expected, predicted, _INDEX, len(_URGENCIES) - 1)


def ordinal_linear_weighted_kappa(
    expected: Sequence[int], predicted: Sequence[int]
) -> float | None:
    """Return linear weighted Cohen's kappa over the 1-5 judge score scale.

    Unlike :func:`linear_weighted_kappa`, this operates on integer scores
    rather than named urgency tiers, and its degenerate-case behavior is
    intentionally different: it returns ``None`` -- not ``1.0`` -- whenever
    either side of the pair carries only a single distinct value (including
    empty input), since a kappa computed from a one-class side is not a
    meaningful agreement statistic for calibration reporting.
    """
    _validate_ordinal_pairs(expected, predicted)
    if not expected:
        return None
    if len(set(expected)) == 1 or len(set(predicted)) == 1:
        return None

    return _weighted_kappa_core(expected, predicted, _ORDINAL_INDEX, len(_ORDINAL_SCORES) - 1)


@dataclass(frozen=True)
class BootstrapInterval:
    """A percentile bootstrap confidence interval for an ordinal kappa."""

    low: float | None
    high: float | None
    skipped: int


def bootstrap_kappa_interval(
    expected: Sequence[int],
    predicted: Sequence[int],
    samples: int = _DEFAULT_BOOTSTRAP_SAMPLES,
) -> BootstrapInterval:
    """Return a deterministic percentile bootstrap interval for ordinal kappa.

    Resamples ``len(expected)`` paired observations with replacement, exactly
    ``samples`` times, using a fixed-seed ``random.Random(0)`` so the result
    is reproducible across runs. A resample whose expected or predicted side
    collapses to a single value yields an undefined
    :func:`ordinal_linear_weighted_kappa` (``None``) and is excluded from the
    interval rather than counted as agreement or disagreement; ``skipped``
    reports how many resamples were excluded this way. When every resample is
    excluded, ``low`` and ``high`` are both ``None``.
    """
    _validate_ordinal_pairs(expected, predicted)
    count = len(expected)
    if count == 0:
        raise ValueError("bootstrap_kappa_interval requires at least one observation")

    rng = random.Random(_BOOTSTRAP_SEED)
    kappas: list[float] = []
    skipped = 0
    for _ in range(samples):
        indices = [rng.randrange(count) for _ in range(count)]
        resampled_expected = [expected[i] for i in indices]
        resampled_predicted = [predicted[i] for i in indices]
        kappa = ordinal_linear_weighted_kappa(resampled_expected, resampled_predicted)
        if kappa is None:
            skipped += 1
        else:
            kappas.append(kappa)

    if not kappas:
        return BootstrapInterval(low=None, high=None, skipped=skipped)

    kappas.sort()
    return BootstrapInterval(
        low=_percentile(kappas, _BOOTSTRAP_LOW_PERCENTILE),
        high=_percentile(kappas, _BOOTSTRAP_HIGH_PERCENTILE),
        skipped=skipped,
    )


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


def _weighted_kappa_core(
    expected: Sequence[_T],
    predicted: Sequence[_T],
    index: Mapping[_T, int],
    span: int,
) -> float:
    """Shared linear weighted kappa computation over an ordered label set.

    Callers must validate shapes and handle degenerate (empty or
    single-valued) inputs before calling this helper -- it assumes
    ``expected``/``predicted`` are non-empty and already known to need the
    full observed-vs-chance agreement computation.
    """

    def weight(actual: _T, prediction: _T) -> float:
        return abs(index[actual] - index[prediction]) / span

    count = len(expected)
    expected_counts = Counter(expected)
    predicted_counts = Counter(predicted)
    observed = (
        sum(
            weight(actual, prediction)
            for actual, prediction in zip(expected, predicted, strict=True)
        )
        / count
    )
    chance = sum(
        weight(actual, prediction)
        * expected_counts[actual]
        * predicted_counts[prediction]
        / (count * count)
        for actual in index
        for prediction in index
    )
    return 1.0 if chance == 0 else 1.0 - observed / chance


def _validate_pairs(expected: Sequence[str], predicted: Sequence[str]) -> None:
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted urgencies must have equal lengths")
    invalid = (set(expected) | set(predicted)) - set(_URGENCIES)
    if invalid:
        raise ValueError(f"unknown urgency values: {', '.join(sorted(invalid))}")


def _validate_ordinal_pairs(expected: Sequence[int], predicted: Sequence[int]) -> None:
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted scores must have equal lengths")
    invalid = {
        value
        for value in (*expected, *predicted)
        if isinstance(value, bool) or value not in _ORDINAL_SCORE_SET
    }
    if invalid:
        raise ValueError(f"scores must be integers from 1 to 5, got: {sorted(invalid)}")
