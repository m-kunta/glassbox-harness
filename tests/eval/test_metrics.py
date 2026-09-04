from glassbox.eval.metrics import (
    linear_weighted_kappa,
    operational_metrics,
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
