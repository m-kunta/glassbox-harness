import pytest

from glassbox.eval.metrics import (
    BootstrapInterval,
    bootstrap_kappa_interval,
    linear_weighted_kappa,
    operational_metrics,
    ordinal_linear_weighted_kappa,
    urgency_confusion_matrix,
)


def test_linear_weighted_kappa_is_one_for_perfect_agreement() -> None:
    assert linear_weighted_kappa(["LOW", "HIGH"], ["LOW", "HIGH"]) == 1.0


def test_linear_weighted_kappa_penalizes_more_distant_sla_misses() -> None:
    adjacent = linear_weighted_kappa(["HIGH", "LOW"], ["MEDIUM", "LOW"])
    distant = linear_weighted_kappa(["HIGH", "LOW"], ["LOW", "LOW"])

    assert distant < adjacent < 1.0


def test_linear_weighted_kappa_handles_one_class_inputs() -> None:
    assert linear_weighted_kappa(["HIGH", "HIGH"], ["HIGH", "HIGH"]) == 1.0
    assert linear_weighted_kappa(["HIGH", "HIGH"], ["MEDIUM", "MEDIUM"]) == 0.0


def test_urgency_confusion_matrix_contains_all_sla_tiers() -> None:
    matrix = urgency_confusion_matrix(["HIGH"], ["MEDIUM"])

    assert set(matrix) == {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert set(matrix["HIGH"]) == {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert matrix["HIGH"]["MEDIUM"] == 1


def test_operational_metrics_aggregate_latency_cost_tokens_and_errors() -> None:
    metrics = operational_metrics(
        [
            {"latency_ms": 10, "cost_usd": 0.02, "tokens": 20},
            {"latency_ms": 30, "cost_usd": 0.04, "tokens": 40},
        ],
        error_count=1,
    )

    assert metrics == {
        "cost_per_decision": 0.03,
        "error_rate": 0.5,
        "p50_latency_ms": 20.0,
        "p95_latency_ms": 29.0,
        "tokens_per_decision": 30.0,
        "total_cost_usd": 0.06,
        "total_tokens": 60,
    }


def test_operational_metrics_interpolates_p95_latency() -> None:
    metrics = operational_metrics(
        [{"latency_ms": value} for value in (0, 100, 200, 300)], error_count=0
    )

    assert metrics["p95_latency_ms"] == 285.0


def test_ordinal_linear_weighted_kappa_is_one_for_perfect_agreement() -> None:
    assert ordinal_linear_weighted_kappa([1, 2, 3, 4, 5], [1, 2, 3, 4, 5]) == 1.0


def test_ordinal_linear_weighted_kappa_penalizes_more_distant_score_misses() -> None:
    adjacent = ordinal_linear_weighted_kappa([1, 3, 5], [2, 3, 4])
    mismatched = ordinal_linear_weighted_kappa([1, 3, 5], [5, 3, 1])

    assert mismatched < adjacent < 1.0
    assert adjacent == pytest.approx(0.5714285714285714)
    assert mismatched == pytest.approx(-0.5)


def test_ordinal_linear_weighted_kappa_returns_none_when_either_side_is_single_valued() -> None:
    assert ordinal_linear_weighted_kappa([3, 3, 3], [3, 3, 3]) is None
    assert ordinal_linear_weighted_kappa([3, 3, 3], [1, 2, 3]) is None
    assert ordinal_linear_weighted_kappa([1, 2, 3], [3, 3, 3]) is None


def test_ordinal_linear_weighted_kappa_returns_none_for_empty_sequences() -> None:
    assert ordinal_linear_weighted_kappa([], []) is None


def test_ordinal_linear_weighted_kappa_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError):
        ordinal_linear_weighted_kappa([1, 2], [1])


def test_ordinal_linear_weighted_kappa_rejects_scores_outside_one_to_five() -> None:
    with pytest.raises(ValueError):
        ordinal_linear_weighted_kappa([0, 2], [1, 2])
    with pytest.raises(ValueError):
        ordinal_linear_weighted_kappa([1, 6], [1, 2])


def test_bootstrap_kappa_interval_is_deterministic_and_matches_the_seeded_computation() -> None:
    expected = [1, 2, 3, 4, 5] * 4
    predicted = [1, 2, 2, 4, 5, 1, 3, 3, 4, 5, 2, 2, 3, 5, 5, 1, 2, 4, 4, 4]

    first = bootstrap_kappa_interval(expected, predicted)
    second = bootstrap_kappa_interval(expected, predicted)

    assert first == second
    assert first == BootstrapInterval(
        low=pytest.approx(0.635036496350365),
        high=pytest.approx(0.932231638418079),
        skipped=0,
    )


def test_bootstrap_kappa_interval_excludes_degenerate_resamples_from_the_interval() -> None:
    expected = [1, 1, 1, 1, 5]
    predicted = [1, 1, 1, 1, 5]

    interval = bootstrap_kappa_interval(expected, predicted, samples=20)

    assert interval == BootstrapInterval(low=1.0, high=1.0, skipped=2)


def test_bootstrap_kappa_interval_returns_none_bounds_when_every_resample_is_degenerate() -> None:
    interval = bootstrap_kappa_interval([3], [3], samples=10)

    assert interval == BootstrapInterval(low=None, high=None, skipped=10)


def test_bootstrap_kappa_interval_uses_exactly_one_thousand_resamples_by_default() -> None:
    expected = [1, 2, 3, 4, 5] * 4
    predicted = [1, 2, 2, 4, 5, 1, 3, 3, 4, 5, 2, 2, 3, 5, 5, 1, 2, 4, 4, 4]

    default_samples = bootstrap_kappa_interval(expected, predicted)
    explicit_samples = bootstrap_kappa_interval(expected, predicted, samples=1_000)

    assert default_samples == explicit_samples
