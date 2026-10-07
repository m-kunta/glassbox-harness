"""Pure outcome reconciliation contracts, policies, and reports."""

import hashlib
import json
import tomllib
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from glassbox.eval.reconciliation import (
    ImportSummary,
    OutcomeInput,
    ReconciliationDecisionSource,
    ReconciliationError,
    ReconciliationOutcomeSource,
    ReconciliationPolicy,
    RejectedOutcome,
    calculate_report,
    default_policy_path,
    derive_label,
    load_policy,
    parse_jsonl,
)

AS_OF = datetime(2026, 10, 5, tzinfo=UTC)
POLICY = """policy_version = "reconciliation_v1"
maturity_days = 30

[[rules]]
agent_name = "replenishment-triage"
decision_type = "triage"
outcome_type = "stockout_occurred"
recommendation_pointer = "/action"
positive_recommendation_values = ["expedite", "order"]
outcome_pointer = "/occurred"
positive_outcome_value = true
"""
DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAX"


@pytest.fixture
def policy(tmp_path: Path) -> ReconciliationPolicy:
    path = tmp_path / "policy.toml"
    path.write_text(POLICY)
    return load_policy(path)


def decision(**changes: object) -> ReconciliationDecisionSource:
    return replace(
        ReconciliationDecisionSource(
            decision_id=DECISION_ID,
            agent_name="replenishment-triage",
            decision_type="triage",
            recommendation={"action": "order"},
            decided_at=AS_OF - timedelta(days=31),
        ),
        **changes,
    )


def outcome(**changes: object) -> OutcomeInput:
    return replace(
        OutcomeInput(
            line_number=1,
            source_id="stockout-1",
            decision_id=DECISION_ID,
            outcome_type="stockout_occurred",
            observed_at=AS_OF,
            horizon_days=30,
            value={"occurred": True},
        ),
        **changes,
    )


def stored_outcome(policy: ReconciliationPolicy, label: str, **changes: object):
    return replace(
        ReconciliationOutcomeSource(
            outcome_id="01ARZ3NDEKTSV4RRFFQ69G5FAY",
            outcome_type="stockout_occurred",
            observed_at=AS_OF,
            label=label,
            reconciliation_policy_hash=policy.policy_hash,
            reconciliation_policy_version=policy.policy_version,
        ),
        **changes,
    )


@pytest.mark.parametrize(
    ("action", "occurred", "label"),
    [("order", True, "tp"), ("order", False, "fp"), ("hold", True, "fn"), ("hold", False, "tn")],
)
def test_rule_derives_every_confusion_matrix_label(policy, action, occurred, label):
    assert (
        derive_label(
            policy,
            decision(recommendation={"action": action}),
            outcome(value={"occurred": occurred}),
        )
        == label
    )


def test_policy_rejects_zero_and_ambiguous_rules(tmp_path: Path, policy):
    path = tmp_path / "invalid.toml"
    path.write_text('policy_version = "v1"\nmaturity_days = 30\nrules = []')
    with pytest.raises(ReconciliationError, match="invalid_policy"):
        load_policy(path)
    path.write_text(POLICY + POLICY[POLICY.index("[[rules]]") :])
    with pytest.raises(ReconciliationError, match="invalid_policy"):
        load_policy(path)
    with pytest.raises(ReconciliationError, match="no_matching_rule"):
        derive_label(policy, decision(agent_name="unknown"), outcome())
    with pytest.raises(ReconciliationError, match="ambiguous_rule"):
        derive_label(replace(policy, rules=policy.rules * 2), decision(), outcome())


@pytest.mark.parametrize(
    "change",
    [
        ("maturity_days = 30", "maturity_days = 0"),
        ("maturity_days = 30", "maturity_days = true"),
        ("maturity_days = 30", "maturity_days = 999999999999999999999"),
        ('policy_version = "reconciliation_v1"', 'policy_version = " "'),
        ('agent_name = "replenishment-triage"', 'agent_name = ""'),
        (
            'positive_recommendation_values = ["expedite", "order"]',
            "positive_recommendation_values = []",
        ),
        (
            'positive_recommendation_values = ["expedite", "order"]',
            'positive_recommendation_values = [{action = "order"}]',
        ),
        ("positive_outcome_value = true", "positive_outcome_value = [true]"),
        ("positive_outcome_value = true", "positive_outcome_value = nan"),
        ("positive_outcome_value = true", 'positive_outcome_value = ""'),
        (
            'positive_recommendation_values = ["expedite", "order"]',
            'positive_recommendation_values = [" "]',
        ),
        ('recommendation_pointer = "/action"', 'recommendation_pointer = ["action"]'),
        ('recommendation_pointer = "/action"', 'recommendation_pointer = "action"'),
        ('recommendation_pointer = "/action"', 'recommendation_pointer = "/~2"'),
        ('policy_version = "reconciliation_v1"', 'unknown = "private"'),
        ('outcome_type = "stockout_occurred"', 'unknown_rule = "private"'),
    ],
)
def test_policy_validation_is_strict(tmp_path: Path, change):
    path = tmp_path / "invalid.toml"
    path.write_text(POLICY.replace(*change))
    with pytest.raises(ReconciliationError, match="invalid_policy"):
        load_policy(path)


def test_policy_hash_is_canonical_and_default_policy_is_versioned(tmp_path: Path, policy):
    path = tmp_path / "formatted.toml"
    path.write_text("# Different formatting\n" + POLICY + "\n")
    assert load_policy(path) == policy
    canonical = json.dumps(tomllib.loads(POLICY), sort_keys=True, separators=(",", ":"))
    assert policy.policy_hash == hashlib.sha256(canonical.encode()).hexdigest()
    assert load_policy(default_policy_path()) == policy
    with pytest.raises(FrozenInstanceError):
        policy.maturity_days = 2


@pytest.mark.parametrize("recommendation", [{}, {"action": []}, {"action": {}}, ["order"]])
def test_pointer_rejects_missing_or_non_scalar_target(policy, recommendation):
    with pytest.raises(ReconciliationError, match="invalid_pointer"):
        derive_label(policy, decision(recommendation=recommendation), outcome())


def test_pointer_decodes_rfc6901_tokens_and_accepts_scalar_root(policy):
    rule = replace(policy.rules[0], recommendation_pointer="/a~1b/~0/", outcome_pointer="")
    assert (
        derive_label(
            replace(policy, rules=(rule,)),
            decision(recommendation={"a/b": {"~": {"": "order"}}}),
            outcome(value=True),
        )
        == "tp"
    )
    with pytest.raises(ReconciliationError, match="invalid_pointer"):
        derive_label(replace(policy, rules=(rule,)), decision(), outcome(value=[]))


def test_scalars_compare_by_json_type(policy):
    rule = replace(policy.rules[0], positive_recommendation_values=(True,))
    policy = replace(policy, rules=(rule,))
    assert derive_label(policy, decision(recommendation={"action": 1}), outcome()) == "fn"
    assert (
        derive_label(
            policy, decision(recommendation={"action": True}), outcome(value={"occurred": 1})
        )
        == "fp"
    )
    rule = replace(rule, positive_recommendation_values=(1,), positive_outcome_value=1)
    assert (
        derive_label(
            replace(policy, rules=(rule,)),
            decision(recommendation={"action": 1.0}),
            outcome(value={"occurred": 1.0}),
        )
        == "tp"
    )


def json_row(**changes: object) -> str:
    row = {
        "source_id": "stockout-1",
        "decision_id": DECISION_ID,
        "outcome_type": "stockout_occurred",
        "observed_at": "2026-10-05T00:00:00Z",
        "horizon_days": 30,
        "value": {"occurred": True},
    }
    row.update(changes)
    return json.dumps(row)


def test_jsonl_parser_keeps_line_number_and_safe_reason():
    parsed = parse_jsonl(
        [
            "\n",
            json_row(),
            '{"source_id": "secret",',
            json_row(source_id="erp-event", horizon_days=-1),
            json_row(observed_at="private-invalid-time"),
            "[]",
        ]
    )
    assert parsed[0] == outcome(line_number=2)
    assert parsed[1:] == (
        RejectedOutcome(3, None, "malformed_json"),
        RejectedOutcome(4, "erp-event", "invalid_shape"),
        RejectedOutcome(5, "stockout-1", "invalid_timestamp"),
        RejectedOutcome(6, None, "invalid_shape"),
    )
    assert "private" not in repr(parsed[1:])
    assert parsed[1].to_dict() == {"line_number": 3, "source_id": None, "reason": "malformed_json"}


@pytest.mark.parametrize(
    "changes",
    [
        {"source_id": ""},
        {"source_id": 12},
        {"decision_id": ""},
        {"decision_id": "not-a-ulid"},
        {"outcome_type": " "},
        {"horizon_days": True},
        {"horizon_days": 1.1},
        {"unknown": "private"},
        {"value": float("nan")},
        {"value": {"amount": float("inf")}},
    ],
)
def test_jsonl_rejects_invalid_shapes(changes):
    assert parse_jsonl([json_row(**changes)]) == (
        RejectedOutcome(1, "stockout-1" if "source_id" not in changes else None, "invalid_shape"),
    )


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-10-05",
        "2026-10-05T00:00:00",
        "2026-10-05T00:00:00-04:00",
        "2026-10-05 00:00:00Z",
        "2026-02-30T00:00:00Z",
        1,
    ],
)
def test_jsonl_rejects_invalid_utc_timestamps(timestamp):
    assert parse_jsonl([json_row(observed_at=timestamp)])[0].reason == "invalid_timestamp"


def test_jsonl_accepts_utc_offsets_fractional_seconds_and_json_values():
    for value in [None, True, 1, 1.5, "value", [], {}]:
        parsed = parse_jsonl([json_row(observed_at="2026-10-05T00:00:00.123+00:00", value=value)])
        assert isinstance(parsed[0], OutcomeInput)
        assert parsed[0].observed_at == AS_OF.replace(microsecond=123000)
        assert parsed[0].value == value
    assert parse_jsonl([]) == ()


def test_report_excludes_young_decisions_and_uses_null_denominators(policy):
    source = [
        decision(outcomes=(stored_outcome(policy, label),)) for label in ("tp", "fp", "tn", "fn")
    ]
    source += [
        decision(has_effective_override=True),
        decision(
            decided_at=AS_OF - timedelta(days=29),
            outcomes=(stored_outcome(policy, "tp"),),
            has_effective_override=True,
        ),
    ]
    report = calculate_report(policy, source, as_of=AS_OF)
    metrics = report.to_dict()["totals"]
    assert metrics == {
        "mature": 5,
        "labelled": 4,
        "coverage": 0.8,
        "tp": 1,
        "fp": 1,
        "tn": 1,
        "fn": 1,
        "precision": 0.5,
        "recall": 0.5,
        "overrides": 1,
        "override_rate": 0.2,
    }
    assert report.to_dict()["by_decision_type"]["triage"] == metrics
    assert report.to_dict()["by_agent_name"]["replenishment-triage"]["triage"] == metrics
    empty = calculate_report(policy, [], as_of=AS_OF).to_dict()["totals"]
    assert empty["coverage"] is empty["precision"] is empty["recall"] is None
    assert empty["override_rate"] is None
    negative = calculate_report(
        policy, [decision(outcomes=(stored_outcome(policy, "tn"),))], as_of=AS_OF
    ).to_dict()["totals"]
    assert negative["precision"] is negative["recall"] is None
    json.dumps(report.to_dict(), allow_nan=False)


def test_report_keeps_policy_hash_cohorts_separate_and_uses_latest_outcome(policy):
    outcomes = (
        stored_outcome(policy, "tp", observed_at=AS_OF - timedelta(days=2)),
        stored_outcome(policy, "fn", outcome_id="a", observed_at=AS_OF - timedelta(days=1)),
        stored_outcome(policy, "fp", outcome_id="b", observed_at=AS_OF - timedelta(days=1)),
        stored_outcome(policy, "tn", reconciliation_policy_hash="other"),
        stored_outcome(policy, "tn", outcome_type="other"),
    )
    report = calculate_report(policy, [decision(outcomes=outcomes)], as_of=AS_OF).to_dict()
    assert report["totals"]["fp"] == 1
    assert report["totals"]["tp"] == report["totals"]["tn"] == report["totals"]["fn"] == 0
    assert report["policy_version"] == policy.policy_version
    assert report["policy_hash"] == policy.policy_hash
    assert report["as_of"] == AS_OF.isoformat()
    assert report["maturity_days"] == 30


def test_report_boundary_grouping_and_unsupported_decisions(policy):
    source = [
        decision(decided_at=AS_OF - timedelta(days=30), has_effective_override=True),
        decision(decision_type="other", has_effective_override=False),
    ]
    report = calculate_report(policy, source, as_of=AS_OF).to_dict()
    assert report["totals"]["mature"] == 2
    assert report["by_decision_type"]["triage"]["override_rate"] == 1
    assert report["by_decision_type"]["other"]["override_rate"] == 0


@pytest.mark.parametrize(
    "as_of", [AS_OF.replace(tzinfo=None), AS_OF.astimezone(timezone(timedelta(hours=1)))]
)
def test_report_requires_utc_as_of(policy, as_of):
    with pytest.raises(ReconciliationError, match="invalid_timestamp"):
        calculate_report(policy, [], as_of=as_of)


def test_import_summary_serializes_counts():
    assert ImportSummary(accepted=2, replayed=1, rejected=3).to_dict() == {
        "accepted": 2,
        "replayed": 1,
        "rejected": 3,
    }
