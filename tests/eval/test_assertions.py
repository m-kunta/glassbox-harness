from glassbox.eval.assertions import evaluate_deterministic
from glassbox.eval.models import DecisionResult, EvidenceRecord

RECOMMENDATION_SCHEMA = {
    "type": "object",
    "required": ["urgency", "action"],
    "properties": {
        "urgency": {"enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]},
        "action": {"type": "string", "minLength": 1},
    },
    "additionalProperties": False,
}


def _valid_result(*, action: str = "Call supplier") -> DecisionResult:
    return DecisionResult(
        decision={"urgency": "HIGH", "action": action},
        evidence=(
            EvidenceRecord(evidence_id="inventory", fields={"units_on_hand": 0}),
            EvidenceRecord(evidence_id="demand", fields={"days_of_supply": 0.0}),
            EvidenceRecord(evidence_id="impact", fields={"lost_sales": 1000}),
        ),
        rationale_citations=("inventory", "demand", "impact"),
        alternatives_considered=("Wait until next delivery",),
    )


def test_evaluate_deterministic_passes_all_checks_for_no_action_decision() -> None:
    checks = evaluate_deterministic(_valid_result(action="Do nothing"), RECOMMENDATION_SCHEMA)

    assert {check.name: check.passed for check in checks} == {
        "schema_valid": True,
        "citations_resolve": True,
        "evidence_present": True,
        "alternatives_present": True,
    }


def test_evaluate_deterministic_reports_each_failed_check() -> None:
    result = DecisionResult(
        decision={"urgency": "UNKNOWN"},
        evidence=(EvidenceRecord(evidence_id="inventory", fields={}),),
        rationale_citations=("missing",),
        alternatives_considered=(),
    )

    checks = evaluate_deterministic(result, RECOMMENDATION_SCHEMA)

    assert {check.name: check.passed for check in checks} == {
        "schema_valid": False,
        "citations_resolve": False,
        "evidence_present": False,
        "alternatives_present": False,
    }
