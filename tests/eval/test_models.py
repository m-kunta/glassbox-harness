from pydantic import ValidationError
import pytest

from glassbox.eval.models import DecisionResult, EvidenceRecord, GoldenCase


def test_golden_case_preserves_typed_case_data() -> None:
    case = GoldenCase(
        case_id="critical-oos-001",
        input={"exception_id": "exc-001", "days_of_supply": 0},
        expected_labels={"urgency": "CRITICAL"},
        metadata={"category": "routine"},
    )

    assert case.case_id == "critical-oos-001"
    assert case.input["days_of_supply"] == 0
    assert case.expected_labels == {"urgency": "CRITICAL"}


def test_golden_case_rejects_blank_case_id() -> None:
    with pytest.raises(ValidationError, match="case_id"):
        GoldenCase(case_id="", input={}, expected_labels={})


def test_decision_result_captures_structured_decision_evidence_and_error() -> None:
    result = DecisionResult(
        decision={"urgency": "HIGH", "action": "Call supplier"},
        evidence=(
            EvidenceRecord(
                evidence_id="inventory_position",
                fields={"units_on_hand": 0},
            ),
        ),
        rationale_citations=("inventory_position",),
        measurements={"latency_ms": 12.5, "tokens_in": 100},
        error={"type": "RuntimeError", "message": "provider unavailable"},
    )

    assert result.evidence[0].evidence_id == "inventory_position"
    assert result.rationale_citations == ("inventory_position",)
    assert result.error == {"type": "RuntimeError", "message": "provider unavailable"}
