# P3/P4 Remediation v2 Design

**Status:** Draft — remediation direction approved; pending written-spec review

## Purpose

Correct verified P3 drift-monitoring and P4 outcome-reconciliation defects before
resuming P3 counterfactual prompt implementation. This work makes shipped reports
usable against real SDK telemetry, preserves audit history during policy corrections,
and prevents operator mistakes from silently creating misleading state.

## Scope

This remediation delivers:

- `reconciliation_v2`, based on the replenishment agent's stable urgency enum;
- policy-scoped external-outcome identity, so a source observation can be re-imported
  under a corrected policy without mutating prior policy history;
- `drift_v2`, whose signals are explicitly enabled by policy and whose baseline window
  is an immutable CLI-supplied snapshot rather than a date embedded in a package file;
- historical and new-trace latency measurement;
- calibrated, deterministic specificity tests for the drift defaults;
- safe, useful CLI diagnostics, migration crash/re-entry handling, and integrity
  constraints; and
- a small amendment to the unimplemented P3 counterfactual design/plan.

It does not add cost estimation or cost ingestion, a new error-rate signal, live
counterfactual execution, a drift scheduler, notifications, or automatic remediation.

## Reconciliation v2

`glassbox/eval/policies/reconciliation_v2.toml` is a new immutable policy artifact:

```toml
policy_version = "reconciliation_v2"
maturity_days = 30

[[rules]]
agent_name = "replenishment-triage"
decision_type = "triage"
outcome_type = "stockout_occurred"
recommendation_pointer = "/urgency"
positive_recommendation_values = ["CRITICAL", "HIGH"]
outcome_pointer = "/occurred"
positive_outcome_value = true
```

`CRITICAL` and `HIGH` are positive predictions because they are the agent's stable,
enumerated escalation levels. A free-text action is not a reliable categorical
prediction. `reconciliation_v1` remains readable and reportable as an historical
record; no row is updated or relabelled in place.

### Policy-scoped observations

An imported external observation is idempotent for exactly
`(source_id, reconciliation_policy_hash)`. Replaying the same payload in that cohort
returns the original row; changing any source payload field in that cohort is rejected.
The same `source_id` may be imported under a different policy hash, producing a new,
append-only row. A migration replaces the source-only unique index with the composite
partial unique index.

### Temporal validity

On import, Glassbox reads the referenced decision and rejects an outcome unless:

- `observed_at >= decision.decided_at`;
- `observed_at <= decision.decided_at + horizon_days`; and
- `horizon_days` equals the selected policy's `maturity_days`.

These bounds make an exported stockout observation attributable to the decision's
declared evaluation period. Rejections go to the existing JSONL reject file with stable
reason codes and do not write outcome rows.

## Drift v2

### Policy shape and baseline lifecycle

`drift_v2.toml` contains only `policy_version`, `recent_duration`, and a non-empty
subset of the four named signal tables. Omitting a table disables that signal; it never
causes the engine to infer that missing source values are healthy. The policy hash
covers precisely that enabled set and every threshold.

The shipped policy enables only:

- `confidence` (decision PSI);
- `decision_type` (decision PSI).

It omits `trace_latency_ms` until the operator has sufficient trace cadence and omits
`trace_cost_usd` until an explicit cost-ingestion API exists. Reports emit
`monitored_signals` and expose omitted known signals as `disabled`, rendered as **Not
monitored (disabled by policy)**. Persisted baseline/run/result rows contain only enabled
signals. An enabled signal with inadequate data remains `insufficient_data` and can make
the aggregate report insufficient.

Both shipped PSI tables use a baseline minimum of 500, a recent minimum of 200, additive
smoothing `0.5`, warning threshold `0.10`, and alert threshold `0.16`. Confidence uses
five fixed `[0.0, 0.2, 0.4, 0.6, 0.8, 1.0]` bins. Decision type retains at most the nine
most frequent baseline values plus `other`; ties break by literal decision-type text.
This fixed ten-category maximum makes a single set of thresholds statistically meaningful
across agents.

Baseline dates leave the policy. The command is:

```shell
glassbox drift baseline --agent AGENT --policy POLICY --start UTC_Z --end UTC_Z
```

`--agent`, `--policy`, `--start`, and `--end` are declared once on the baseline parser.
Both timestamps use strict UTC `Z` text. The materialized window is half-open
`[start, end)`, must satisfy `start < end` and `end <= created_at`, and is stored on the
immutable baseline already supported by the schema. Re-windowing with the same policy is
an explicit `--supersede-baseline` operation, linked to its predecessor. Creation emits
a warning, but succeeds, when the baseline overlaps the current recent window.

Before a baseline exists, the web route reports only `baseline_not_created`; it cannot
know whether an operator-selected future window will have enough data. A stored baseline
whose CUSUM variance is zero reports `baseline_has_no_variance`, never the misleading
"wait for more data" reason.

### Latency and cost

Repository reads derive trace latency for `status = 'ok'` rows with both timestamps as
integer microseconds using Python's timezone-aware datetime subtraction. This supplies
historical samples even when `traces.latency_ms` is null. `status = 'error'` traces and
rows lacking `ended_at` are excluded from latency samples. The shipped policy leaves this
signal disabled until an operator elects to monitor a cohort with adequate trace cadence.

SDK completion events additionally populate `TraceEvent.latency_ms` from the captured
start/end timestamps. Cost remains null unless a future, explicit SDK cost-ingestion API
supplies it; it is not estimated.

### Metric calibration and bounded output

Before committing thresholds, deterministic fixed-seed stationary simulations exercise
normal and lognormal trace samples plus stable confidence and decision-type populations
at the policy's minimum and representative larger sample sizes. The selected v2
thresholds must keep alert false positives at or below 5% for every tested stationary
population while retaining existing seeded-shift detection tests. This is a release gate,
not a runtime simulation.

An enabled trace CUSUM table must declare `transform = "none"` or `transform = "log1p"`
and `evaluation_samples`. The v2 latency example uses `log1p`, a baseline minimum of 200,
and exactly the 100 most-recent valid traces from the requested recent window. It uses
warning threshold `5.2`, alert threshold `7.0`, and the existing reference value `0.5` in
transformed standard-deviation units. Baseline mean/deviation and CUSUM samples use the
declared transform; diagnostics retain raw latency count, mean, and median alongside the
transformed calculation summary. This bounds a report's false-alarm behavior independently
of agent throughput.

CUSUM details retain only a bounded diagnostic summary: final positive/negative values,
peak magnitude, and peak sample index. They no longer persist or render an unbounded
per-sample path.

## Safety and integrity repairs

- Policy/baseline CLI failures print curated, non-sensitive diagnostics: invalid policy
  text and deficient signals may be named, but database paths, SQLite messages, and
  credentials are never emitted.
- `outcomes import` rejects a nonexistent or non-file database before any writable
  `Database.open()` call, preventing accidental empty database creation.
- Schema execution runs each migration in a transaction. Upgrade code rechecks the
  current strict fingerprint after acquiring its write lock, making retries/re-entry
  deterministic rather than exposing raw duplicate-object failures.
- The remediation migration adds database-level coherence checks for drift run/result
  status reasons and a unique partial index on `drift_baselines.supersedes_baseline_id`.
  It preserves valid historical rows and rejects malformed new state.
- UI action copy maps `drift_detected` with no human action to **Review drift report**,
  never to **No operator action is required**. The drift page includes the policy hash.
- Remove tracked `.superpowers/sdd/` scratch reports from Git tracking while retaining
  each local file for its owner.

## Counterfactual amendment before implementation

The existing P3 counterfactual plan is amended before its Task 1 begins:

- Provider prompts receive structured redacted decision facts, cited evidence,
  alternatives, trace metadata, and optional capture content read from an explicit
  blob directory; they do not receive the original recommendation or rationale.
- Stored counterfactual results retain structured snapshot facts, capture references,
  prompt artifact hash/version, provider/model, and a hash of the rendered provider
  input. They do not persist optional capture content inline in SQLite.
- Reports call the experiment a **prompt-and-model counterfactual** unless the stored
  source model and configured variant provider/model are provably identical. No result
  claims to isolate prompt effects otherwise.
- A run with zero successful cases exits non-zero after persisting its auditable failed
  results. Partial case failures remain successful command execution with exit zero.

## Verification

- TDD regression tests reproduce every confirmed issue: trace latency derivation,
  optional/disabled policy signals, parser inheritance, safe nonexistent-database
  failure, migration interruption/retry, composite outcome identity, v2 re-import,
  temporal outcome bounds, and UI wording/provenance.
- Fixed-seed stationary drift tests prove v2 thresholds meet the false-positive gate;
  existing shifted-population tests prove sensitivity remains.
- Full suite, Ruff, mypy, import-linter, schema-mirror, and read-only no-write tests
  remain green.
- Documentation shows a v2 baseline command with explicit timestamps and explains cost
  is disabled by the shipped policy rather than silently considered healthy.
