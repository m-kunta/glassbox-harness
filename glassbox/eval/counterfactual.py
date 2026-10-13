"""Pure prompt artifact and response contracts for counterfactual evaluation."""

from __future__ import annotations

import hashlib
import json
import math
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from glassbox.events.models import canonical_dumps

_SNAPSHOT_TOKEN = "{{decision_snapshot_json}}"
_CAPTURE_INPUT_CONTRACT = "input_only_no_prior_outputs"


@dataclass(frozen=True)
class PromptArtifact:
    prompt_version: str
    prompt_hash: str
    body: str
    capture_input_contract: str | None = None


@dataclass(frozen=True)
class CounterfactualParseResult:
    recommendation: Any
    rationale: str


def load_prompt_artifact(path: Path) -> PromptArtifact:
    """Validate exact TOML delimiters and identify the complete raw artifact."""
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("prompt artifact must be UTF-8") from None
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "+++":
        raise ValueError("prompt artifact must begin with exact +++ front matter")
    end = next((index for index in range(1, len(lines))
                if lines[index].rstrip("\r\n") == "+++"), None)
    if end is None:
        raise ValueError("prompt artifact is missing its closing +++ delimiter")
    try:
        metadata = tomllib.loads("".join(lines[1:end]))
    except tomllib.TOMLDecodeError:
        raise ValueError("invalid prompt front matter") from None
    if set(metadata) - {"prompt_version", "capture_input_contract"}:
        raise ValueError("unknown prompt metadata")
    version = metadata.get("prompt_version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError("prompt_version must be a non-empty string")
    contract = metadata.get("capture_input_contract")
    if "capture_input_contract" in metadata and contract != _CAPTURE_INPUT_CONTRACT:
        raise ValueError("unsupported capture_input_contract")
    body = "".join(lines[end + 1:])
    if body.count(_SNAPSHOT_TOKEN) != 1:
        raise ValueError("prompt body must contain exactly one snapshot placeholder")
    return PromptArtifact(version, hashlib.sha256(raw).hexdigest(), body, contract)


def render_prompt(artifact: PromptArtifact, snapshot: Mapping[str, Any]) -> str:
    """Insert canonical JSON without interpreting any instructions in the data."""
    if artifact.body.count(_SNAPSHOT_TOKEN) != 1:
        raise ValueError("prompt body must contain exactly one snapshot placeholder")
    _validate_json_value(snapshot)
    return artifact.body.replace(_SNAPSHOT_TOKEN, canonical_dumps(snapshot), 1)


def parse_counterfactual_response(raw: str) -> CounterfactualParseResult:
    """Accept one exact response envelope containing finite, UTF-8-safe JSON."""
    try:
        payload = json.loads(raw, parse_constant=_reject_non_finite_constant)
    except (ValueError, TypeError, RecursionError):
        raise ValueError("invalid counterfactual JSON response") from None
    if not isinstance(payload, dict) or set(payload) != {"recommendation", "rationale"}:
        raise ValueError("counterfactual response must contain recommendation and rationale only")
    rationale = payload["rationale"]
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError("counterfactual rationale must be a non-empty string")
    try:
        _validate_json_value(payload)
    except (UnicodeError, RecursionError):
        raise ValueError("counterfactual response must contain UTF-8-safe JSON") from None
    return CounterfactualParseResult(payload["recommendation"], rationale)


def _reject_non_finite_constant(token: str) -> Any:
    raise ValueError("non-finite JSON constants are not accepted")


def _validate_json_value(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            key.encode("utf-8")
            _validate_json_value(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_json_value(item)
    elif isinstance(value, str):
        value.encode("utf-8")
    elif value is None or isinstance(value, (bool, int)):
        return
    elif isinstance(value, float) and math.isfinite(value):
        return
    else:
        raise ValueError("value must be finite JSON-compatible data")
