from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from glassbox.eval.counterfactual import (
    load_prompt_artifact,
    parse_counterfactual_response,
    render_prompt,
)

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
