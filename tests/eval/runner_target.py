from glassbox.eval.models import DecisionResult, EvidenceRecord, GoldenCase


def run_case(case: GoldenCase) -> DecisionResult:
    del case
    return DecisionResult(
        decision={"urgency": "HIGH", "action": "Review"},
        evidence=(
            EvidenceRecord(evidence_id="inventory", fields={}),
            EvidenceRecord(evidence_id="demand", fields={}),
            EvidenceRecord(evidence_id="impact", fields={}),
        ),
        rationale_citations=("inventory", "demand", "impact"),
        alternatives_considered=("Do nothing",),
    )


def raise_error(case: GoldenCase) -> DecisionResult:
    del case
    raise RuntimeError("scripted target failure")
