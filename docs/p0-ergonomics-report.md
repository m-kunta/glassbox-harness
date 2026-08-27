# P0 acceptance gate and SDK ergonomics report

**Status: P1 is not approved.** This document records the P0 evidence and the
remaining follow-ups; it is not an approval to begin P1.

## Benchmark evidence

The benchmark uses a deterministic, representative replenishment decision:
the same inventory-gap calculation runs with and without P0 instrumentation.
The instrumented path creates one trace, retrieval span, decision, and
single-field evidence event, backed by a bounded `Collector` and temporary
SQLite database. It uses five unmeasured warm-up runs followed by 30 baseline
and 30 instrumented runs. Operation timing excludes the asynchronous drain,
which is reported separately as `flush_ms`.

Command executed from the repository root on 2026-08-27:

```shell
/opt/homebrew/bin/python3.11 benchmarks/p0_overhead.py
```

Result:

```json
{"accepted": true, "baseline_median_ms": 0.00008312053978443146, "baseline_p95_ms": 0.00020866282284259796, "baseline_runs": 30, "dropped_events": 0, "failed_events": 0, "flush_ms": 12.722958344966173, "instrumented_median_ms": 0.031854258850216866, "instrumented_p95_ms": 0.07286702748388052, "instrumented_runs": 30, "overhead_ms": 0.031771138310432434, "threshold_ms": 25}
```

The acceptance rule is `overhead_ms < max(0.05 * baseline_ms, 25)`. The
observed overhead is `0.031771138310432434 ms`; the threshold is `25 ms`, so
this run passes. The result also recorded zero queue drops and zero repository
write failures. The small baseline makes the 25 ms floor decisive. This is a
P0 SDK/persistence microbenchmark, not an end-to-end production triage latency
claim.

Repeat with `python benchmarks/p0_overhead.py`; `--runs` rejects values below
30 and `--warmup` rejects values below 1.

## Validation evidence

Commands executed from the repository root on 2026-08-27:

```shell
PATH=/Users/MKunta/Library/Python/3.11/bin:$PATH pytest -q
PATH=/Users/MKunta/Library/Python/3.11/bin:$PATH ruff check .
PATH=/Users/MKunta/Library/Python/3.11/bin:$PATH mypy glassbox
PATH=/Users/MKunta/Library/Python/3.11/bin:$PATH lint-imports
```

Results:

```text
139 passed in 1.83s
All checks passed!
Success: no issues found in 17 source files
Contracts: 4 kept, 0 broken.
```

The benchmark itself has a focused test, `tests/test_p0_overhead_benchmark.py`, that verifies
the minimum sample size, the reported medians/p95s/flush time, the acceptance
formula, and zero collector loss in its controlled run.

## Integration ergonomics

The replenishment integration is commit `0919aa2` (merged by `85db078`). Its
agent file changed **16 lines** (14 additions and 2 removals); the only
instrumentation statement in `TriageAgent.run` is `@trace`. It does not change
triage branching, inputs, outputs, or error handling. The optional-import
fallback is a 3-line identity decorator, so importing the agent still works
when Glassbox is absent.

SDK touchpoints are intentionally small:

- The agent imports `trace` and applies one `@trace` decorator.
- The host-side example performs one `gb.init(...)` call and one
  `gb.flush(...)` call; it calls `collector.shutdown(...)` directly.
- The agent's business logic contains no span, decision, evidence, collector,
  or persistence calls.

Disabled-mode behavior has direct SDK test coverage: with `GLASSBOX_ENABLED=0`,
the wrapped decision returns `"agent result"`, records zero collector events,
and both flush and shutdown return successfully. The benchmark's instrumented
run recorded `dropped_events: 0` and `failed_events: 0`; collector loss is
therefore not observed in this acceptance run.

Awkward points and recommended changes for a later approved phase:

1. Application setup must assemble `Database`, `Repository`, and `Collector`
   before calling `gb.init`. Consider an opt-in `init_sqlite(...)` convenience
   function after real-host lifecycle feedback.
2. The host must retain the collector and explicitly flush/shutdown it. Consider
   a context-managed configuration helper that also owns database closure.
3. The optional fallback is verified only at import/identity-decorator level;
   add the required clean-environment representative `TriageAgent.run` test
   before treating the integration as fully portable.

## Remaining P0 follow-ups and approval decision

The following TODO items remain unchecked and are P0 acceptance blockers:

1. **Database migration:** databases created before strict UTC RFC3339 storage
   validation have no rebuild migration. Existing deployments could be unable
   to upgrade safely; this needs a migration and test evidence.
2. **No-Glassbox execution:** the agent repository only tests module import
   fallback. It has not executed a representative `TriageAgent.run` in an
   environment where Glassbox is unavailable, so runtime fallback behavior is
   not yet evidenced.

Consequently, P0 has benchmark and validation evidence but does **not** satisfy
the approval gate. Obtain explicit P1 approval only after these follow-ups are
resolved and reviewed; do not start P1 from this report.
