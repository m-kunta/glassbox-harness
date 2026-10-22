from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from glassbox.eval import counterfactual
from glassbox.eval.counterfactual import (
    load_prompt_artifact,
    parse_counterfactual_response,
    render_prompt,
)
from glassbox.eval.counterfactual_config import CounterfactualConfig
from glassbox.eval.reconciliation import load_policy
from glassbox.events import DecisionEvent, EvidenceEvent, SpanEvent, TraceEvent
from glassbox.events.models import canonical_dumps
from glassbox.store import Database, Repository
from glassbox.store.blobs import BlobStore
from glassbox.store.repository import OutcomeSubmission

TOKEN = "{{decision_snapshot_json}}"


def test_artifact_hashes_raw_bytes_and_renders_canonical_snapshot(tmp_path: Path) -> None:
    raw = b'+++\nprompt_version = "v1"\n+++\nFacts: {{decision_snapshot_json}}\n'
    path = tmp_path / "prompt.md"
    path.write_bytes(raw)
    artifact = load_prompt_artifact(path)
    assert artifact.prompt_version == "v1"
    assert artifact.prompt_hash == hashlib.sha256(raw).hexdigest()
    assert artifact.capture_input_contract is None
    assert render_prompt(artifact, {"z": [False, None], "a": "café"}) == (
        'Facts: {"a":"café","z":[false,null]}\n'
    )
    path.write_bytes(raw.replace(b"\n", b"\r\n"))
    assert load_prompt_artifact(path).prompt_hash != artifact.prompt_hash


def test_input_capture_contract_is_explicit(tmp_path: Path) -> None:
    path = tmp_path / "prompt.md"
    path.write_text('+++\nprompt_version="v1"\n'
                    'capture_input_contract="input_only_no_prior_outputs"\n+++\n' + TOKEN)
    assert load_prompt_artifact(path).capture_input_contract == "input_only_no_prior_outputs"


@pytest.mark.parametrize("text", [
    TOKEN, ' +++\nprompt_version="v1"\n+++\n' + TOKEN,
    '+++\nprompt_version="v1"\n',
    '+++\nprompt_version=""\n+++\n' + TOKEN,
    '+++\nprompt_version="  "\n+++\n' + TOKEN,
    '+++\nprompt_version=1\n+++\n' + TOKEN,
    '+++\nprompt_version="v1"\nextra=true\n+++\n' + TOKEN,
    '+++\nprompt_version="v1"\ncapture_input_contract="anything"\n+++\n' + TOKEN,
    '+++\nprompt_version="v1"\ncapture_input_contract=42\n+++\n' + TOKEN,
    '+++\nprompt_version="v1"\n+++\nmissing token',
    '+++\nprompt_version="v1"\n+++\n' + TOKEN * 2,
    '+++\nprompt_version="v1"\n+++-\n' + TOKEN,
    '+++\ninvalid TOML\n+++\n' + TOKEN,
])
def test_malformed_artifacts_rejected(tmp_path: Path, text: str) -> None:
    path = tmp_path / "prompt.md"
    path.write_text(text)
    with pytest.raises(ValueError):
        load_prompt_artifact(path)


def test_non_utf8_artifact_rejected(tmp_path: Path) -> None:
    path = tmp_path / "prompt.md"
    path.write_bytes(b"\xff")
    with pytest.raises(ValueError):
        load_prompt_artifact(path)


@pytest.mark.parametrize("recommendation", [
    None, True, 2, 0.25, "café", [1, None], {"action": "review", "nested": [False, {}]},
])
def test_parser_accepts_any_safe_json_recommendation(recommendation: object) -> None:
    result = parse_counterfactual_response(json.dumps({
        "recommendation": recommendation, "rationale": "A grounded reason",
    }))
    assert result.recommendation == recommendation
    assert result.rationale == "A grounded reason"


@pytest.mark.parametrize("raw", [
    "not json", "[]", "null", '{}', '{"recommendation":{}}',
    '{"recommendation":{},"rationale":null}',
    '{"recommendation":{},"rationale":"  "}',
    '{"recommendation":{},"rationale":"ok","extra":0}',
    '{"recommendation":NaN,"rationale":"ok"}',
    '{"recommendation":{"x":Infinity},"rationale":"ok"}',
    '{"recommendation":1e999,"rationale":"ok"}',
    '{"recommendation":"\\ud800","rationale":"ok"}',
    '{"recommendation":{"\\ud800":1},"rationale":"ok"}',
    '{"recommendation":null,"rationale":"\\ud800"}',
])
def test_parser_rejects_malformed_or_unsafe_payloads(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_counterfactual_response(raw)


def test_shipped_artifact_is_valid() -> None:
    artifact = load_prompt_artifact(Path("glassbox/eval/prompts/counterfactual_v1.md"))
    assert artifact.prompt_version == "counterfactual_v1"
    assert TOKEN in artifact.body


DECIDED = datetime(2026, 8, 1, 12, tzinfo=UTC)
AS_OF = DECIDED + timedelta(days=45)
TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FA0"
SPAN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FA1"
DECISION_IDS = (
    "01ARZ3NDEKTSV4RRFFQ69G5FA2",
    "01ARZ3NDEKTSV4RRFFQ69G5FA3",
    "01ARZ3NDEKTSV4RRFFQ69G5FA4",
)
CONFIG = CounterfactualConfig(
    "openai", "variant-model", "test-credential", "https://api.openai.com/v1", True,
)


class FakeProvider:
    def __init__(self, responses: list[str | Exception]) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[str, str]] = []
        self.timeouts: list[float | None] = []

    def complete(
        self, system_prompt: str, user_prompt: str, *, timeout_seconds: float | None = None,
    ) -> str:
        self.calls.append((system_prompt, user_prompt))
        self.timeouts.append(timeout_seconds)
        answer = next(self.responses)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _answer(urgency: str = "CRITICAL") -> str:
    return json.dumps({
        "recommendation": {"urgency": urgency}, "rationale": "hypothetical reasoning",
    })


def _policy():
    return load_policy(Path("glassbox/eval/policies/reconciliation_v2.toml"))


def _prompt(*, contract: bool = False):
    artifact = load_prompt_artifact(Path("glassbox/eval/prompts/counterfactual_v1.md"))
    if contract:
        from dataclasses import replace
        return replace(artifact, capture_input_contract="input_only_no_prior_outputs")
    return artifact


def _seed(
    path: Path, *, count: int = 1, urgency: str = "LOW",
    input_ref: str | None = None, prompt_ref: str | None = None,
    completion_ref: str | None = None, source_provider: str | None = "openai",
    source_model: str | None = "variant-model", outcome: bool = False,
) -> None:
    database = Database.open(path)
    try:
        repository = Repository(database)
        repository.write_event(TraceEvent(
            trace_id=TRACE_ID, agent_name="replenishment-triage",
            agent_version="source-v1", started_at=DECIDED - timedelta(hours=1),
            environment="shadow", input_ref=input_ref,
        ))
        repository.write_event(SpanEvent(
            span_id=SPAN_ID, trace_id=TRACE_ID, name="recommend", span_kind="llm",
            started_at=DECIDED - timedelta(minutes=1), model=source_model,
            prompt_ref=prompt_ref, completion_ref=completion_ref,
            attributes={"gen_ai.provider.name": source_provider} if source_provider else {},
        ))
        for index in range(count):
            decision_id = DECISION_IDS[index]
            repository.write_event(DecisionEvent(
                decision_id=decision_id, trace_id=TRACE_ID,
                agent_name="replenishment-triage", agent_version="source-v1",
                entity_type="sku_dc", entity_id=f"entity-{index}",
                decision_type="triage",
                recommendation={"urgency": urgency, "private_original": "ORIGINAL_RECOMMENDATION"},
                rationale="ORIGINAL_RATIONALE", rationale_citations=("inventory", "missing"),
                confidence=0.71, alternatives_considered=({"action": "wait"},),
                decided_at=DECIDED + timedelta(minutes=index),
            ))
            for field_name, units in (("on_hand", 2), ("open_orders", 5)):
                repository.write_event(EvidenceEvent(
                    evidence_id="inventory", decision_id=decision_id,
                    source_system="ERP", source_ref="inventory-row",
                    field_name=field_name, field_value={"units": units}, weight=0.8,
                    retrieved_at=DECIDED - timedelta(minutes=10),
                ))
        if outcome:
            policy = _policy()
            repository.record_outcome(OutcomeSubmission(
                outcome_id="01ARZ3NDEKTSV4RRFFQ69G5FA5", source_id="stockout-1",
                decision_id=DECISION_IDS[0], outcome_type="stockout_occurred",
                observed_at=DECIDED + timedelta(days=1), horizon_days=30,
                value={"occurred": True, "private_outcome": "OUTCOME_VALUE"},
                label="fn", reconciliation_policy_version=policy.policy_version,
                reconciliation_policy_hash=policy.policy_hash,
            ))
    finally:
        database.close()


def _execute(
    path: Path, provider: FakeProvider, *, prompt=None, max_cases: int | None = None,
    blob_dir: Path | None = None, as_of: datetime = AS_OF,
    **kwargs: Any,
) -> tuple[Any, Any]:
    assert hasattr(counterfactual, "run_counterfactual"), "counterfactual runner is missing"
    report = counterfactual.run_counterfactual(
        path, CONFIG, prompt or _prompt(), _policy(), DECIDED - timedelta(days=1),
        max_cases, blob_dir, clock=lambda: as_of, provider_factory=lambda _: provider,
        **kwargs,
    )
    database = Database.open_read_only(path)
    try:
        detail = Repository(database).counterfactual_run_detail(report.run_id)
        assert detail is not None
        return report, detail
    finally:
        database.close()


def test_live_shadow_run_persists_one_case_without_outcome_enrichment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "events.sqlite3"
    _seed(path, outcome=True)
    database = Database.open_read_only(path)
    try:
        candidate = Repository(database).counterfactual_candidate(
            DECISION_IDS[0], _policy().policy_hash,
        )
    finally:
        database.close()
    assert candidate is not None

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("live run must not select candidates or derive outcomes")

    monkeypatch.setattr(Repository, "counterfactual_candidates", forbidden)
    monkeypatch.setattr(counterfactual, "_enrich_result", forbidden)
    provider = FakeProvider([_answer()])
    report, detail = _execute(
        path, provider, preselected_candidates=(candidate,),
        execution_mode="live_shadow", enrich_outcomes=False, request_timeout_seconds=10.0,
    )
    assert report.run.execution_mode == detail.run.execution_mode == "live_shadow"
    assert report.selected_count == report.succeeded_count == 1
    assert detail.results[0].outcome_status == "outcome_not_applicable"
    assert detail.results[0].outcome_id is detail.results[0].outcome_label is None
    assert provider.timeouts == [10.0]
    for forbidden_text in (
        "ORIGINAL_RECOMMENDATION", "ORIGINAL_RATIONALE", "OUTCOME_VALUE",
        '"confidence"', '"feedback"', '"outcomes"',
    ):
        assert forbidden_text not in "".join(provider.calls[0])


@pytest.mark.parametrize("options", [
    {"execution_mode": "invalid"}, {"execution_mode": "live_shadow"},
])
def test_runner_rejects_invalid_execution_mode_options(tmp_path: Path, options: dict) -> None:
    path = tmp_path / "events.sqlite3"
    _seed(path)
    with pytest.raises(ValueError):
        _execute(path, FakeProvider([]), **options)


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), -float("inf")])
def test_runner_rejects_invalid_timeout_before_provider_call(
    tmp_path: Path, timeout: float,
) -> None:
    path = tmp_path / "events.sqlite3"
    _seed(path)
    provider = FakeProvider([])
    with pytest.raises(ValueError):
        _execute(path, provider, request_timeout_seconds=timeout)
    assert provider.calls == []


def test_runner_persists_provider_timeout_and_continues(tmp_path: Path) -> None:
    from glassbox.eval.counterfactual_provider import CounterfactualProviderTimeout

    path = tmp_path / "events.sqlite3"
    _seed(path, count=2)
    provider = FakeProvider([CounterfactualProviderTimeout("private timeout"), _answer()])
    report, detail = _execute(path, provider, request_timeout_seconds=10.0)
    assert (report.failed_count, report.succeeded_count) == (1, 1)
    assert detail.results[0].failure_code == "provider_timeout"
    assert "private timeout" not in json.dumps(report.to_dict())


def test_run_sends_only_cited_facts_and_persists_audit_data(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite3"
    _seed(path, outcome=True)
    provider = FakeProvider([_answer()])
    report, detail = _execute(path, provider)
    assert report.succeeded_count == 1
    system_prompt, user_prompt = provider.calls[0]
    assert "BEGIN UNTRUSTED DECISION DATA" in user_prompt
    assert "END UNTRUSTED DECISION DATA" in user_prompt
    for forbidden in (
        "ORIGINAL_RECOMMENDATION", "ORIGINAL_RATIONALE", "OUTCOME_VALUE",
        '"confidence"', '"feedback"', '"free_text"',
    ):
        assert forbidden not in system_prompt + user_prompt
    result = detail.results[0]
    assert result.original_recommendation["private_original"] == "ORIGINAL_RECOMMENDATION"
    assert result.original_rationale == "ORIGINAL_RATIONALE"
    assert result.snapshot_facts["entity_id"] == "entity-0"
    assert len(result.snapshot_facts["evidence_groups"][0]["fields"]) == 2
    assert result.snapshot_facts["unresolved_citations"] == ["missing"]
    expected_hash = hashlib.sha256(canonical_dumps({
        "system_prompt": system_prompt, "user_prompt": user_prompt,
    }).encode("utf-8")).hexdigest()
    assert result.input_hash == expected_hash
    assert detail.run.as_of == AS_OF


def test_capture_input_is_opt_in_and_never_persisted_or_mixed_with_outputs(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite3"
    blobs = BlobStore(tmp_path / "blobs")
    input_ref = blobs.put(b"INPUT_CAPTURE_ONLY")
    prompt_ref = blobs.put(b"SPAN_PROMPT_FORBIDDEN")
    completion_ref = blobs.put(b"COMPLETION_FORBIDDEN")
    _seed(path, input_ref=input_ref, prompt_ref=prompt_ref, completion_ref=completion_ref)
    plain_provider = FakeProvider([_answer()])
    _execute(path, plain_provider, blob_dir=tmp_path / "blobs")
    assert "INPUT_CAPTURE_ONLY" not in plain_provider.calls[0][1]
    provider = FakeProvider([_answer()])
    _, detail = _execute(path, provider, prompt=_prompt(contract=True), blob_dir=tmp_path / "blobs")
    assert "INPUT_CAPTURE_ONLY" in provider.calls[0][1]
    assert "SPAN_PROMPT_FORBIDDEN" not in provider.calls[0][1]
    assert "COMPLETION_FORBIDDEN" not in provider.calls[0][1]
    result = detail.results[0]
    assert result.capture_references["trace_input_ref"] == input_ref
    assert result.capture_references["span_prompt_refs"] == [prompt_ref]
    assert result.capture_references["span_completion_refs"] == [completion_ref]
    with sqlite3.connect(path) as connection:
        serialized = repr(connection.execute(
            "SELECT * FROM counterfactual_results WHERE result_id = ?", (result.result_id,),
        ).fetchone())
    assert "INPUT_CAPTURE_ONLY" not in serialized


@pytest.mark.parametrize(("source_provider", "source_model", "parity"), [
    ("openai", "variant-model", "matched"),
    ("claude", "variant-model", "different"),
    (None, "variant-model", "unknown"),
])
def test_report_requires_proven_model_and_provider_parity(
    tmp_path: Path, source_provider: str | None, source_model: str | None, parity: str,
) -> None:
    path = tmp_path / "events.sqlite3"
    _seed(path, source_provider=source_provider, source_model=source_model)
    report, detail = _execute(path, FakeProvider([_answer()]))
    assert detail.results[0].model_parity == parity
    expected = "prompt-only counterfactual" if parity == "matched" else (
        "prompt-and-model counterfactual"
    )
    assert report.to_dict()["description"] == expected


def test_run_persists_failed_case_and_continues_after_bad_provider_response(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite3"
    _seed(path, count=2)
    provider = FakeProvider(["not JSON", _answer()])
    report, detail = _execute(path, provider)
    assert (report.selected_count, report.succeeded_count, report.failed_count) == (2, 1, 1)
    assert [item.failure_code for item in detail.results] == ["invalid_response", None]
    assert [item.decision_id for item in detail.results] == [DECISION_IDS[1], DECISION_IDS[0]]
    assert detail.results[0].input_hash is not None


@pytest.mark.parametrize(("original", "variant"), [
    ("LOW", "CRITICAL"), ("MEDIUM", "HIGH"),
])
def test_run_derives_variant_label_from_hypothetical_recommendation_and_raw_outcome(
    tmp_path: Path, original: str, variant: str,
) -> None:
    path = tmp_path / "events.sqlite3"
    _seed(path, urgency=original, outcome=True)
    report, detail = _execute(path, FakeProvider([_answer(variant)]))
    assert report.to_dict()["results"][0]["outcome_status"] == "labelled"
    assert detail.results[0].outcome_label == "tp"
    assert detail.results[0].outcome_id == "01ARZ3NDEKTSV4RRFFQ69G5FA5"


def test_run_leaves_outcome_label_null_when_decision_is_not_mature(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite3"
    _seed(path, outcome=True)
    report, detail = _execute(path, FakeProvider([_answer()]), as_of=DECIDED + timedelta(days=2))
    assert detail.results[0].outcome_id is None
    assert detail.results[0].outcome_label is None
    assert report.to_dict()["results"][0]["outcome_status"] == "decision_not_mature"
    from glassbox.eval.counterfactual import CounterfactualReport
    assert CounterfactualReport(detail.run, detail.results, tuple(
        item.outcome_status for item in detail.results
    )).to_dict()["results"][0]["outcome_status"] == "decision_not_mature"


def test_run_uses_fixed_newest_first_selection_before_max_cases(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite3"
    _seed(path, count=3)
    report, detail = _execute(path, FakeProvider([_answer(), _answer()]), max_cases=2)
    assert report.selected_count == 2
    assert [item.decision_id for item in detail.results] == [DECISION_IDS[2], DECISION_IDS[1]]


def test_future_decisions_do_not_consume_max_cases_or_reach_provider(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite3"
    _seed(path, count=2)
    provider = FakeProvider([_answer()])
    report, detail = _execute(
        path, provider, max_cases=1, as_of=DECIDED + timedelta(seconds=30),
    )
    assert report.selected_count == 1
    assert len(provider.calls) == 1
    assert [item.decision_id for item in detail.results] == [DECISION_IDS[0]]


@pytest.mark.parametrize(("input_ref", "contract", "use_blob_dir", "diagnostic"), [
    ("f" * 64, True, False, "blob_dir_not_supplied"),
    ("f" * 64, False, True, "capture_contract_not_declared"),
    (None, True, True, "input_ref_absent"),
])
def test_structured_only_capture_skip_reason_is_persisted_and_reported(
    tmp_path: Path, input_ref: str | None, contract: bool,
    use_blob_dir: bool, diagnostic: str,
) -> None:
    path = tmp_path / "events.sqlite3"
    _seed(path, input_ref=input_ref)
    provider = FakeProvider([_answer()])
    report, detail = _execute(
        path, provider, prompt=_prompt(contract=contract),
        blob_dir=tmp_path / "unreadable-private-path" if use_blob_dir else None,
    )
    assert report.succeeded_count == 1
    assert detail.results[0].snapshot_facts["capture_diagnostic"] == diagnostic
    assert report.to_dict()["results"][0]["capture_diagnostic"] == diagnostic
    assert "capture_diagnostic" not in provider.calls[0][1]
    assert "unreadable-private-path" not in json.dumps(report.to_dict())


def test_blob_read_failure_becomes_a_case_result_not_a_command_failure(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite3"
    _seed(path, count=2, input_ref="f" * 64)
    report, detail = _execute(
        path, FakeProvider([]), prompt=_prompt(contract=True), blob_dir=tmp_path / "absent",
    )
    assert report.failed_count == 2
    assert [item.failure_code for item in detail.results] == ["capture_unavailable"] * 2
    assert all(item.input_hash is None for item in detail.results)
    assert "absent" not in json.dumps(report.to_dict())


def test_invalid_blob_reference_is_a_case_failure_and_does_not_abort_the_run(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.sqlite3"
    _seed(path, input_ref="not-a-content-address", prompt_ref="also-invalid")
    report, detail = _execute(
        path, FakeProvider([]), prompt=_prompt(contract=True), blob_dir=tmp_path / "blobs",
    )
    assert report.failed_count == 1
    assert detail.results[0].failure_code == "capture_invalid_reference"
    assert detail.results[0].capture_references["trace_input_ref"] is None
    assert detail.results[0].capture_references["span_prompt_refs"] == []


def test_provider_error_does_not_stop_later_cases(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite3"
    _seed(path, count=2)
    provider = FakeProvider([RuntimeError("private provider body"), _answer()])
    report, detail = _execute(path, provider)
    assert report.failed_count == 1
    assert report.succeeded_count == 1
    assert detail.results[0].failure_code == "provider_failure"
    assert "private provider body" not in json.dumps(report.to_dict())


def test_unmatched_policy_rule_retains_success_without_outcome_label(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite3"
    _seed(path, outcome=True)
    database = Database.open(path)
    try:
        with database.connection:
            database.connection.execute(
                "UPDATE decisions SET decision_type = ? WHERE decision_id = ?",
                ("unmatched", DECISION_IDS[0]),
            )
    finally:
        database.close()
    report, detail = _execute(path, FakeProvider([_answer()]))
    assert detail.results[0].failure_code is None
    assert detail.results[0].outcome_id is None
    assert report.to_dict()["results"][0]["outcome_status"] == "outcome_not_applicable"
