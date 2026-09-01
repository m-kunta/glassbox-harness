from glassbox.eval.metrics import linear_weighted_kappa, urgency_confusion_matrix


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
