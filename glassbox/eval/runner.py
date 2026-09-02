"""Manifest-driven deterministic evaluation without agent-runtime dependencies."""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, cast

import yaml

from .assertions import evaluate_deterministic
from .metrics import linear_weighted_kappa
from .models import DecisionResult, GoldenCase
from .target import load_target


def run_suite(manifest_path: Path) -> dict[str, Any]:
    """Run one YAML suite and return a canonical JSON-compatible result."""
    manifest = _load_yaml(manifest_path)
    base = manifest_path.parent
    with _invoking_project_on_import_path():
        target = load_target(_required_string(manifest, "target"))
    schema = _load_json(base / _required_string(manifest, "schema"))
    cases = [_load_case(base / path) for path in _required_list(manifest, "cases")]
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("suite contains duplicate case_id values")

    case_results: list[dict[str, Any]] = []
    assertion_totals = {name: 0 for name in _ASSERTION_NAMES}
    expected: list[str] = []
    predicted: list[str] = []
    for case in cases:
        result = _run_case(target, case)
        checks = evaluate_deterministic(result, schema)
        for check in checks:
            assertion_totals[check.name] += int(check.passed)
        expected.append(str(case.expected_labels["urgency"]))
        predicted.append(str(result.decision.get("urgency", "LOW")))
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
    metrics = {"urgency_agreement": linear_weighted_kappa(expected, predicted)}
    gates = _gates(manifest.get("gates", {}), assertions, metrics)
    return {"assertions": assertions, "cases": case_results, "gates": gates, "metrics": metrics}


_ASSERTION_NAMES = ("schema_valid", "citations_resolve", "evidence_present", "alternatives_present")


@contextmanager
def _invoking_project_on_import_path() -> Iterator[None]:
    """Let a console invocation import an adapter owned by its project."""
    project_root = str(Path.cwd())
    sys.path.insert(0, project_root)
    try:
        yield
    finally:
        sys.path.remove(project_root)


def _run_case(target: Any, case: GoldenCase) -> DecisionResult:
    try:
        return cast(DecisionResult, target(case))
    except Exception as exc:  # targets must not abort the remaining suite
        return DecisionResult(
            decision={},
            evidence=(),
            rationale_citations=(),
            error={"type": type(exc).__name__, "message": str(exc)},
        )


def _gates(config: Any, assertions: dict[str, float], metrics: dict[str, float]) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ValueError("gates must be a mapping")
    values = {"deterministic_pass_rate": min(assertions.values()), **metrics}
    failed = {
        name: value
        for name, threshold in config.items()
        if (value := values.get(name, 0.0)) < threshold
    }
    return {"failed": failed, "passed": not failed}


def _load_case(path: Path) -> GoldenCase:
    return GoldenCase.model_validate(_load_yaml(path))


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text())
    except OSError as exc:
        raise ValueError(f"unable to read {path}") from exc
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
