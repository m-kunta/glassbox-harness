from pathlib import Path

import pytest
import yaml

from glassbox.eval.runner import _gates, run_suite


def _write_suite(tmp_path: Path) -> Path:
    (tmp_path / "schema.json").write_text(
        '{"type":"object","required":["urgency","action"]}'
    )
    (tmp_path / "case.yaml").write_text(
        yaml.safe_dump(
            {
                "case_id": "case-001",
                "input": {},
                "expected_labels": {"urgency": "HIGH"},
            }
        )
    )
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "target": "tests.eval.runner_target:run_case",
                "schema": "schema.json",
                "cases": ["case.yaml"],
                "gates": {"deterministic_pass_rate": 1.0, "urgency_agreement": 0.6},
            }
        )
    )
    return manifest


def test_run_suite_returns_case_checks_metrics_and_passing_gates(tmp_path: Path) -> None:
    result = run_suite(_write_suite(tmp_path))

    assert result["cases"][0]["case_id"] == "case-001"
    assert result["assertions"]["schema_valid"] == 1.0
    assert result["metrics"]["urgency_agreement"] == 1.0
    assert result["gates"]["passed"] is True


def test_run_suite_records_target_exception_and_continues(tmp_path: Path) -> None:
    manifest = _write_suite(tmp_path)
    data = yaml.safe_load(manifest.read_text())
    data["target"] = "tests.eval.runner_target:raise_error"
    manifest.write_text(yaml.safe_dump(data))

    result = run_suite(manifest)

    assert result["cases"][0]["error"]["type"] == "RuntimeError"
    assert result["gates"]["passed"] is False


def test_run_suite_loads_an_agent_owned_target_from_the_invoking_project(
    tmp_path: Path, monkeypatch
) -> None:
    (tmp_path / "integrations").mkdir()
    (tmp_path / "integrations" / "__init__.py").write_text("")
    (tmp_path / "integrations" / "target.py").write_text(
        "from glassbox.eval.models import DecisionResult\n"
        "def run_case(case):\n"
        "    return DecisionResult(\n"
        "        decision={'urgency': 'HIGH', 'action': 'review'},\n"
        "        evidence=(),\n"
        "        rationale_citations=(),\n"
        "    )\n"
    )
    manifest = _write_suite(tmp_path)
    data = yaml.safe_load(manifest.read_text())
    data["target"] = "integrations.target:run_case"
    manifest.write_text(yaml.safe_dump(data))
    monkeypatch.chdir(tmp_path.parent)

    result = run_suite(manifest)

    assert result["cases"][0]["case_id"] == "case-001"


def test_gates_fail_when_a_ceiling_metric_exceeds_its_limit() -> None:
    gates = _gates(
        {"error_rate": 0.1, "p95_latency_ms": 100, "cost_per_decision": 1},
        {"schema_valid": 1.0},
        {"error_rate": 0.3, "p95_latency_ms": 120.0, "cost_per_decision": 0.5},
    )

    assert gates == {"failed": {"error_rate": 0.3, "p95_latency_ms": 120.0}, "passed": False}


def test_run_suite_rejects_malformed_yaml_as_a_value_error(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("target: [unterminated")

    with pytest.raises(ValueError, match="unable to parse YAML"):
        run_suite(manifest)


def test_run_suite_rejects_an_invalid_json_schema_as_a_value_error(tmp_path: Path) -> None:
    manifest = _write_suite(tmp_path)
    (tmp_path / "schema.json").write_text('{"type": 12}')

    with pytest.raises(ValueError, match="invalid JSON schema"):
        run_suite(manifest)


def test_run_suite_records_non_result_target_return_as_a_case_error(tmp_path: Path) -> None:
    manifest = _write_suite(tmp_path)
    data = yaml.safe_load(manifest.read_text())
    data["target"] = "tests.eval.runner_target:return_wrong_shape"
    manifest.write_text(yaml.safe_dump(data))

    result = run_suite(manifest)

    assert result["cases"][0]["error"]["type"] == "TypeError"
    assert result["gates"]["passed"] is False


def test_run_suite_records_invalid_target_urgency_as_a_case_error(tmp_path: Path) -> None:
    manifest = _write_suite(tmp_path)
    data = yaml.safe_load(manifest.read_text())
    data["target"] = "tests.eval.runner_target:return_invalid_urgency"
    manifest.write_text(yaml.safe_dump(data))

    result = run_suite(manifest)

    assert result["cases"][0]["error"]["type"] == "ValueError"
    assert result["metrics"]["error_rate"] == 1.0
