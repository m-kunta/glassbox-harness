from __future__ import annotations

import importlib.util
from pathlib import Path


def _benchmark_module():
    path = Path(__file__).parents[1] / "benchmarks" / "p0_overhead.py"
    spec = importlib.util.spec_from_file_location("p0_overhead", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_benchmark_uses_required_sample_sizes_and_reports_acceptance_metrics() -> None:
    benchmark = _benchmark_module()

    parser = benchmark.build_parser()
    assert parser.parse_args([]).runs >= 30
    assert parser.parse_args([]).warmup >= 1

    result = benchmark.run_benchmark(runs=30, warmup=1)

    assert result["baseline_runs"] == 30
    assert result["instrumented_runs"] == 30
    assert result["baseline_median_ms"] >= 0
    assert result["baseline_p95_ms"] >= result["baseline_median_ms"]
    assert result["instrumented_median_ms"] >= 0
    assert result["instrumented_p95_ms"] >= result["instrumented_median_ms"]
    assert result["flush_ms"] >= 0
    assert result["threshold_ms"] == max(0.05 * result["baseline_median_ms"], 25)
    assert result["accepted"] == (result["overhead_ms"] < result["threshold_ms"])
    assert result["dropped_events"] == 0
    assert result["failed_events"] == 0
