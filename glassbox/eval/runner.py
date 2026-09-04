"""Manifest-driven deterministic evaluation without agent-runtime dependencies."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, SchemaError
from pydantic import ValidationError

from .assertions import evaluate_deterministic
from .metrics import linear_weighted_kappa, operational_metrics, urgency_confusion_matrix
from .models import DecisionResult, GoldenCase
from .target import load_target


def run_suite(manifest_path: Path) -> dict[str, Any]:
    """Run one YAML suite and return a canonical JSON-compatible result."""
    manifest = _load_yaml(manifest_path)
    base = manifest_path.parent
    target = load_target(_required_string(manifest, "target"), module_root=base)
    schema = _load_json(base / _required_string(manifest, "schema"))
    cases = [_load_case(base / path) for path in _required_list(manifest, "cases")]
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("suite contains duplicate case_id values")

    case_results: list[dict[str, Any]] = []
    assertion_totals = {name: 0 for name in _ASSERTION_NAMES}
    expected: list[str] = []
    predicted: list[str] = []
    measurements: list[dict[str, float | int]] = []
    error_count = 0
    for case in cases:
        result = _run_case(target, case)
        measurements.append(result.measurements)
        error_count += int(result.error is not None)
        checks = evaluate_deterministic(result, schema)
        for check in checks:
            assertion_totals[check.name] += int(check.passed)
        expected_urgency = _urgency(case.expected_labels.get("urgency"), source="expected")
        predicted_urgency = _urgency(result.decision.get("urgency"), source="target")
        if expected_urgency is not None and predicted_urgency is not None:
            expected.append(expected_urgency)
            predicted.append(predicted_urgency)
        case_results.append(
            {
                "case_id": case.case_id,
                "error": result.error,
                "checks": [check.model_dump() for check in checks],
            }
        )
    count = len(cases)
    assertions = {
        name: passed / count if count else 1.0 for name, passed in assertion_totals.items()
    }
    metrics: dict[str, Any] = {
        "urgency_agreement": linear_weighted_kappa(expected, predicted),
        "urgency_confusion_matrix": urgency_confusion_matrix(expected, predicted),
        **operational_metrics(measurements, error_count=error_count),
    }
    gates = _gates(manifest.get("gates", {}), assertions, metrics)
    return {"assertions": assertions, "cases": case_results, "gates": gates, "metrics": metrics}


_ASSERTION_NAMES = ("schema_valid", "citations_resolve", "evidence_present", "alternatives_present")
_FLOOR_GATES = frozenset({"deterministic_pass_rate", "urgency_agreement"})
_URGENCIES = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})


def _run_case(target: Any, case: GoldenCase) -> DecisionResult:
    try:
        result = target(case)
        if not isinstance(result, DecisionResult):
            raise TypeError("evaluation target must return a DecisionResult")
        urgency = result.decision.get("urgency")
        if urgency is not None and _urgency(urgency, source="target") is None:
            raise ValueError(f"target returned unknown urgency {urgency!r}")
        return result
    except Exception as exc:  # targets must not abort the remaining suite
        return DecisionResult(
            decision={},
            evidence=(),
            rationale_citations=(),
            error={"type": type(exc).__name__, "message": str(exc)},
        )


def _gates(config: Any, assertions: dict[str, float], metrics: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ValueError("gates must be a mapping")
    values = {"deterministic_pass_rate": min(assertions.values()), **metrics}
    failed = {}
    for name, threshold in config.items():
        if not isinstance(threshold, (int, float)):
            raise ValueError(f"gate {name!r} must have a numeric threshold")
        if name not in values or not isinstance(values[name], (int, float)):
            raise ValueError(f"gate {name!r} does not name a numeric metric")
        value = values[name]
        if name in _FLOOR_GATES and value < threshold:
            failed[name] = value
        elif name not in _FLOOR_GATES and value > threshold:
            failed[name] = value
    return {"failed": failed, "passed": not failed}


def _load_case(path: Path) -> GoldenCase:
    try:
        return GoldenCase.model_validate(_load_yaml(path))
    except ValidationError as exc:
        raise ValueError(f"invalid case file {path}: {exc}") from exc


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text())
    except OSError as exc:
        raise ValueError(f"unable to read {path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"unable to parse YAML {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a mapping")
    return value


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read JSON schema {path}") from exc
    if not isinstance(value, dict):
        raise ValueError("schema must be an object")
    try:
        Draft202012Validator.check_schema(value)
    except SchemaError as exc:
        raise ValueError(f"invalid JSON schema {path}: {exc.message}") from exc
    return value


def _required_string(mapping: dict[str, Any], name: str) -> str:
    value = mapping.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"suite requires non-empty {name}")
    return value


def _required_list(mapping: dict[str, Any], name: str) -> list[str]:
    value = mapping.get(name)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"suite requires {name} as a list of paths")
    return value


def _urgency(value: Any, *, source: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in _URGENCIES:
        raise ValueError(f"{source} urgency must be one of {sorted(_URGENCIES)}")
    return value
