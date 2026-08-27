"""Measure representative P0 tracing overhead against the same agent work uninstrumented."""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
from collections.abc import Callable
from pathlib import Path
from time import perf_counter
from typing import TypedDict

import glassbox as gb
from glassbox.collector import Collector
from glassbox.sdk.config import reset_for_testing
from glassbox.store import Database, Repository


class BenchmarkResult(TypedDict):
    baseline_runs: int
    instrumented_runs: int
    baseline_median_ms: float
    baseline_p95_ms: float
    instrumented_median_ms: float
    instrumented_p95_ms: float
    overhead_ms: float
    flush_ms: float
    threshold_ms: float
    accepted: bool
    dropped_events: int
    failed_events: int


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line interface for the repeatable acceptance benchmark."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=30, help="Runs per scenario (minimum: 30).")
    parser.add_argument("--warmup", type=int, default=5, help="Unmeasured runs per scenario.")
    return parser


def representative_agent_work() -> int:
    """Minimal deterministic replenishment decision work shared by both scenarios."""
    on_hand = 4
    reorder_point = 10
    return max(reorder_point - on_hand, 0)


@gb.trace
def instrumented_agent_work() -> int:
    """The representative work plus one P0 trace, span, decision, and evidence."""
    with gb.span("inventory_lookup", kind="retrieval"):
        recommendation = representative_agent_work()
    with gb.decision_context(
        entity_type="sku_dc", entity_id="sku-100_dc-01", decision_type="replenish"
    ) as decision:
        gb.evidence(
            evidence_id="inventory_position",
            source_system="inventory",
            source_ref="sku-100/dc-01",
            fields={"on_hand": 4},
        )
        decision.complete(
            recommendation={"reorder_quantity": recommendation},
            rationale="On-hand inventory is below the reorder point.",
            rationale_citations=["inventory_position"],
            confidence=0.9,
            alternatives_considered=[{"reorder_quantity": 0}],
        )
    return recommendation


def run_benchmark(*, runs: int, warmup: int) -> BenchmarkResult:
    """Run baseline and SQLite-backed instrumentation scenarios and return their metrics."""
    if runs < 30:
        raise ValueError("runs must be at least 30")
    if warmup < 1:
        raise ValueError("warmup must be at least 1")

    for _ in range(warmup):
        representative_agent_work()
    baseline_samples = _timed_runs(representative_agent_work, runs)

    with tempfile.TemporaryDirectory(prefix="glassbox-p0-overhead-") as directory:
        database = Database.open(Path(directory) / "glassbox.sqlite3")
        collector = Collector(Repository(database), capacity=10_000)
        try:
            gb.init(agent="replenishment-triage", version="p0-benchmark", collector=collector)
            for _ in range(warmup):
                instrumented_agent_work()
            instrumented_samples = _timed_runs(instrumented_agent_work, runs)
            flush_started = perf_counter()
            flush_ok = gb.flush(timeout=10.0)
            flush_ms = (perf_counter() - flush_started) * 1_000
            if not flush_ok:
                raise RuntimeError("collector did not flush benchmark events within 10 seconds")
            dropped_events = collector.dropped_events
            failed_events = collector.failed_events
        finally:
            gb.shutdown(timeout=10.0)
            reset_for_testing()
            database.close()

    baseline_median_ms = statistics.median(baseline_samples)
    instrumented_median_ms = statistics.median(instrumented_samples)
    overhead_ms = instrumented_median_ms - baseline_median_ms
    threshold_ms = max(0.05 * baseline_median_ms, 25)
    return {
        "baseline_runs": runs,
        "instrumented_runs": runs,
        "baseline_median_ms": baseline_median_ms,
        "baseline_p95_ms": _p95(baseline_samples),
        "instrumented_median_ms": instrumented_median_ms,
        "instrumented_p95_ms": _p95(instrumented_samples),
        "overhead_ms": overhead_ms,
        "flush_ms": flush_ms,
        "threshold_ms": threshold_ms,
        "accepted": overhead_ms < threshold_ms,
        "dropped_events": dropped_events,
        "failed_events": failed_events,
    }


def _timed_runs(function: Callable[[], int], runs: int) -> list[float]:
    return [_timed_call(function) for _ in range(runs)]


def _timed_call(function: Callable[[], int]) -> float:
    started = perf_counter()
    assert function() == 6
    return (perf_counter() - started) * 1_000


def _p95(samples: list[float]) -> float:
    return statistics.quantiles(samples, n=100, method="inclusive")[94]


def main() -> int:
    """Run the benchmark and print stable JSON for the ergonomics report."""
    args = build_parser().parse_args()
    print(json.dumps(run_benchmark(runs=args.runs, warmup=args.warmup), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
