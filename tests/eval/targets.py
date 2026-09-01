from glassbox.eval.models import DecisionResult, GoldenCase


def run_case(case: GoldenCase) -> DecisionResult:
    return DecisionResult(
        decision={"case_id": case.case_id, "urgency": "HIGH"},
        evidence=(),
        rationale_citations=(),
    )


not_a_target = "not callable"
