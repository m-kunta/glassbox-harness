# P3/P4 Remediation v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct drift monitoring and outcome reconciliation so their versioned policies, audit history, CLI behavior, and telemetry measurements are reliable on real Glassbox data.

**Architecture:** Keep drift calculation policy-driven and pure; the repository supplies typed source data and persists only enabled signal rows. A migration upgrades P4 outcome provenance and drift constraints atomically. The CLI validates operator input before opening write connections, while read service and templates expose policy-disabled signals without fabricating persistence rows.

**Tech Stack:** Python 3.11, SQLite/WAL, stdlib `tomllib`/`statistics`/`math`, Pydantic event models, argparse, Jinja2, pytest, Ruff, mypy, import-linter.

## Global Constraints

- `reconciliation_v2` uses `/urgency` and `CRITICAL`/`HIGH` as positive replenishment predictions; v1 history is never edited.
- External outcome replay identity is exactly `(source_id, reconciliation_policy_hash)`; changed payloads inside that cohort fail closed.
- An imported outcome must fall in `[decision.decided_at, decision.decided_at + horizon_days]` and use the selected policy's exact `maturity_days`.
- A drift policy contains a non-empty subset of the four known signals; omitted signals are disabled by policy, not inferred healthy.
- The shipped `drift_v2` enables only confidence and decision type. Latency and cost are visibly disabled until an operator adds their policy tables.
- Baseline windows are CLI-owned strict UTC `Z` timestamps with half-open `[start, end)` semantics; `start < end <= created_at`.
- Latency is derived only for completed `status='ok'` traces, via timezone-aware timestamp subtraction; error and unfinished traces are excluded.
- An enabled latency signal declares `transform` (`none` or `log1p`) and `evaluation_samples`; `log1p` transforms both baseline and recent values.
- No runtime metric simulation, cost estimation, scheduled monitoring, automatic action, or counterfactual provider call is introduced.
- CLI errors may name curated policy/input deficiencies but must not disclose SQLite errors, credentials, or local paths.
- New code follows TDD: observe each focused test fail before implementing production behavior.

---

## File structure

| Path | Responsibility |
| --- | --- |
| `glassbox/eval/policies/drift_v2.toml` | Shipped two-signal default and documented optional latency configuration. |
| `glassbox/eval/drift.py` | Optional-policy parser, bounded PSI/CUSUM calculations, baseline window arguments, and reports. |
| `glassbox/eval/policies/reconciliation_v2.toml` | Corrected replenishment urgency policy. |
| `glassbox/eval/reconciliation.py` | Decision-relative outcome validation and safe import failures. |
| `glassbox/store/migrations/006_p3_p4_remediation.sql` | Atomic schema upgrade for policy-scoped outcome identity and drift coherence constraints. |
| `glassbox/store/database.py` | Exact P4-to-v2 upgrade, schema fingerprints, transaction/re-entry protection. |
| `glassbox/store/repository.py` | Composite outcome idempotency, temporal source reads, optional drift source/result validation. |
| `glassbox/sdk/tracer.py` | Completion-time trace latency. |
| `glassbox/cli.py` | Baseline `--start/--end`, parser-safe policy plumbing, safe diagnostics, and import preflight. |
| `glassbox/web/read_models.py`, `glassbox/web/templates/drift.html` | Disabled-signal, no-variance, policy-hash, and bounded-CUSUM presentation. |
| `tests/eval/test_drift.py`, `tests/eval/test_reconciliation.py` | Pure policy, metric, calibration, and temporal-validation tests. |
| `tests/store/test_database.py`, `tests/store/test_repository.py`, `tests/store/test_schema.py` | Migration, schema, idempotency, and timestamp-source tests. |
| `tests/sdk/test_tracer.py`, `tests/test_cli.py`, `tests/web/test_read_service.py`, `tests/web/test_server.py` | SDK event, CLI, read-only UI, and template behavior tests. |
| `README.md`, `TODO.md`, `docs/superpowers/plans/2026-10-09-glassbox-p3-counterfactual-prompts.md` | Operator documentation, backlog status, and counterfactual safety amendment. |

## Task 1: Make drift policies optional-signal, bounded, and calibrated

**Files:**
- Create: `glassbox/eval/policies/drift_v2.toml`
- Modify: `glassbox/eval/drift.py`
- Modify: `tests/eval/test_drift.py`

**Interfaces:**
- `DriftPolicy` no longer has baseline dates; it exposes `signals: tuple[SignalPolicy, ...]` containing any non-empty distinct subset of `SIGNAL_NAMES`.
- `SignalPolicy` adds `transform: Literal["none", "log1p"]` and `evaluation_samples: int | None`; PSI signals require `transform="none"` and no evaluation sample cap, trace CUSUM signals require both fields.
- `calculate_baseline(policy, decisions, traces, *, baseline_start: datetime, baseline_end: datetime) -> BaselineMaterial` and `calculate_report(policy: DriftPolicy, baseline: BaselineMaterial, recent_decisions: Sequence[DecisionSample], recent_traces: Sequence[TraceSample], as_of: datetime) -> DriftReport` operate only on `policy.signals`.

- [ ] **Step 1: Write focused failing policy and calculation tests.**

  Add tests which parse a two-signal policy, reject an empty policy and an unknown signal table, and prove a missing known signal is not an error. Add a policy fixture with optional latency:

  ```toml
  policy_version = "drift_v2"
  recent_duration = "7d"

  [trace_latency_ms]
  population = "trace"
  algorithm = "cusum"
  minimum_baseline_samples = 200
  minimum_recent_samples = 100
  transform = "log1p"
  evaluation_samples = 100
  reference_value = 0.5
  warning_threshold = 5.2
  alert_threshold = 7.0
  ```

  Add `test_latency_uses_the_100_most_recent_valid_samples_after_log_transform`, seeding 101 valid ordered traces and asserting the oldest is absent from details and the calculated baseline values equal `math.log1p(raw_ms)`. Add `test_zero_variance_baseline_has_no_variance_reason` and `test_disabled_known_signals_are_absent_from_persistable_report_rows`.

  Add fixed-seed stationary tests with `random.Random(20261010)`: confidence uses five uniform bins, decision type uses ten uniform categories, and transformed lognormal latency uses 100 bounded samples. Over 1,000 trials, assert each v2 alert rate is `<= 0.05`; retain shifted-distribution assertions that produce `drift_detected`.

- [ ] **Step 2: Run the tests to verify red.**

  Run:

  ```shell
  .venv/bin/pytest tests/eval/test_drift.py -q
  ```

  Expected: policy construction rejects omitted signal tables and the new transform/evaluation fields are unsupported.

- [ ] **Step 3: Implement the policy and bounded metric behavior.**

  Replace the all-four top-level key check with `{"policy_version", "recent_duration"} <= set(mapping)` plus an exact check that every remaining key is in `SIGNAL_NAMES` and at least one remains. Iterate `policy.signals`, never `SIGNAL_NAMES`, in baseline material, report calculation, stored-material validation, and report reconstruction.

  For decision type, retain the nine largest baseline categories using `sorted(counts, key=lambda name: (-counts[name], name))[:9]`, append `"other"`, and use the same categories for recent data. For confidence, accept `smoothing` and use the v2 five boundaries. For an enabled trace signal, filter null values, transform with `math.log1p` when requested, select `values[-evaluation_samples:]` after chronological ordering, and retain raw count/mean/median plus bounded CUSUM peak/final diagnostics. Return `baseline_has_no_variance` when the sample floor is met but standard deviation is zero.

  Add `drift_v2.toml` with two PSI tables: 500 baseline samples, 200 recent samples, smoothing `0.5`, warning `0.10`, alert `0.16`; five confidence bins; and no trace tables.

- [ ] **Step 4: Verify green.**

  Run:

  ```shell
  .venv/bin/pytest tests/eval/test_drift.py -q
  .venv/bin/ruff check glassbox/eval/drift.py tests/eval/test_drift.py
  .venv/bin/mypy glassbox/eval/drift.py
  ```

  Expected: fixed-seed tests are deterministic, disabled signals do not become healthy rows, and shift tests still detect a change.

- [ ] **Step 5: Commit the pure drift model.**

  ```shell
  git add glassbox/eval/drift.py glassbox/eval/policies/drift_v2.toml tests/eval/test_drift.py
  git commit -m "feat: add bounded optional drift policy"
  ```

## Task 2: Upgrade schema and repository integrity atomically

**Files:**
- Create: `glassbox/store/migrations/006_p3_p4_remediation.sql`
- Modify: `glassbox/store/schema.sql`
- Modify: `glassbox/store/database.py`
- Modify: `glassbox/store/repository.py`
- Modify: `tests/store/test_database.py`
- Modify: `tests/store/test_repository.py`
- Modify: `tests/store/test_schema.py`

**Interfaces:**
- `Repository.record_outcome(submission)` identifies replays by source ID and reconciliation policy hash.
- `Repository.drift_source_data(agent_name: str, baseline_start: datetime, baseline_end: datetime, recent_start: datetime, recent_end: datetime, *, include_baseline: bool = True) -> DriftSourceData` returns completed OK trace samples with integer-microsecond derived latency when the stored latency column is null.
- Drift baseline/run validators accept exactly the policy's enabled signal-name set supplied by the caller, not the historical hard-coded four.

- [ ] **Step 1: Write failing migration and repository tests.**

  Add `test_outcome_source_id_can_be_reused_under_a_new_policy_hash`: insert the same external source with v1 and v2 hashes, assert two rows, then assert a changed value under v2 raises and rolls back. Add `test_outcome_v2_migration_rebuilds_source_identity_without_losing_v1_rows` from an exact P4 fixture.

  Add `test_schema_migration_failure_rolls_back_all_004_objects` by monkeypatching `_execute_schema_statements` to fail during 004 on an exact P3a database, then asserting reopening sees the unchanged P3a fingerprint. Add `test_concurrent_or_reentrant_005_upgrade_rechecks_fingerprint_after_lock` asserting no raw duplicate-index error escapes.

  Add direct SQL tests rejecting a drift result whose status is `insufficient_data` without a reason or a non-insufficient result with a reason, and rejecting two baselines that supersede the same predecessor. Add a repository test proving an `ok` trace with `started_at=2026-10-10T00:00:00.000001Z` and `ended_at=2026-10-10T00:00:00.123457Z` derives exactly `123.456` ms while error and unfinished traces do not yield samples.

- [ ] **Step 2: Run the storage tests to verify red.**

  Run:

  ```shell
  .venv/bin/pytest tests/store/test_database.py tests/store/test_repository.py tests/store/test_schema.py -q
  ```

  Expected: v2 policy replay conflicts on `source_id`, drift constraints are missing, and current migration execution leaves partial DDL in the injected failure test.

- [ ] **Step 3: Implement migration 006 and robust upgrade dispatch.**

  In `006_p3_p4_remediation.sql`, rebuild `outcomes` with unchanged rows copied verbatim, drop `idx_outcomes_source_id`, and create:

  ```sql
  CREATE UNIQUE INDEX idx_outcomes_source_policy
  ON outcomes(source_id, reconciliation_policy_hash)
  WHERE source_id IS NOT NULL;
  ```

  Rebuild `drift_runs`/`drift_results` to add coherent status/reason checks and preserve every valid historical row. Add the partial unique index on non-null `supersedes_baseline_id`. Update every schema-object tuple/fingerprint so `open_read_only()` accepts only the exact v2 schema.

  Wrap each ordinary migration application in `BEGIN IMMEDIATE`/`COMMIT` with rollback. After acquiring the lock, refresh `sqlite_master`; if the target fingerprint is already present, return normally. Keep table-rebuild migrations in their own existing atomic helpers, and call the new 006 rebuild only from an exact v2-predecessor fingerprint.

  In the repository, change lookup to `WHERE source_id = ? AND reconciliation_policy_hash = ?`; compare the same payload fields on a replay. Derive latency with `(ended_at - started_at).total_seconds() * 1_000`, never SQLite `julianday`, and include only OK completed traces. Thread `enabled_signal_names: frozenset[str]` through drift insert validators and assert run/result names exactly equal it.

- [ ] **Step 4: Verify green.**

  Run:

  ```shell
  .venv/bin/pytest tests/store/test_database.py tests/store/test_repository.py tests/store/test_schema.py -q
  .venv/bin/ruff check glassbox/store tests/store
  .venv/bin/mypy glassbox/store
  ```

  Expected: upgrades are retry-safe, v1 and v2 history coexist, and read-only schema checks remain exact.

- [ ] **Step 5: Commit durable remediation storage.**

  ```shell
  git add glassbox/store tests/store
  git commit -m "fix: harden drift and outcome persistence"
  ```

## Task 3: Correct reconciliation validation and trace emission

**Files:**
- Create: `glassbox/eval/policies/reconciliation_v2.toml`
- Modify: `glassbox/eval/reconciliation.py`
- Modify: `glassbox/sdk/tracer.py`
- Modify: `tests/eval/test_reconciliation.py`
- Modify: `tests/sdk/test_tracer.py`

**Interfaces:**
- `import_outcomes(database_path: Path, input_path: Path, reject_path: Path, policy: ReconciliationPolicy, *, clock: Callable[[], datetime]) -> ImportSummary` rejects inputs outside the decision-relative horizon with `ReconciliationError` reason codes `observed_before_decision`, `observed_after_horizon`, and `horizon_mismatch`.
- Completed `TraceEvent` includes numeric `latency_ms`; start-only events keep `latency_ms=None`.

- [ ] **Step 1: Write failing policy, temporal, and trace tests.**

  Add a v2 fixture where a `{"urgency":"HIGH"}` recommendation and `{"occurred":true}` outcome produces `tp`; `MEDIUM` produces `fn`. Add three JSONL import tests with an observation before `decided_at`, after `decided_at + horizon_days`, and a horizon not equal to 30; assert each rejection reason and zero persisted rows.

  Add an SDK trace test with a deterministic `_now` sequence: start `2026-10-10T00:00:00Z`, end `2026-10-10T00:00:00.250000Z`; assert the completion TraceEvent has `latency_ms == 250.0` and the initial event remains null.

- [ ] **Step 2: Run tests to verify red.**

  Run:

  ```shell
  .venv/bin/pytest tests/eval/test_reconciliation.py tests/sdk/test_tracer.py -q
  ```

  Expected: v2 policy is absent, imports accept temporally invalid observations, and trace completions lack latency.

- [ ] **Step 3: Implement policy and validations.**

  Add `reconciliation_v2.toml` exactly as approved. In import flow, resolve the decision before `OutcomeSubmission` construction, compare timezone-aware UTC instants, and write the named rejection without leaking database detail. Update README test fixtures to include both `occurred:true` and explicit negative `occurred:false` examples.

  In `_emit_trace`, when `ended_at` is supplied, add `latency_ms = (ended_at - state.started_at).total_seconds() * 1_000` after asserting non-negative elapsed time. Preserve the SDK's fail-open boundary: malformed clocks or emitter errors never crash the instrumented agent.

- [ ] **Step 4: Verify green.**

  Run:

  ```shell
  .venv/bin/pytest tests/eval/test_reconciliation.py tests/sdk/test_tracer.py -q
  .venv/bin/ruff check glassbox/eval/reconciliation.py glassbox/sdk/tracer.py tests/eval/test_reconciliation.py tests/sdk/test_tracer.py
  .venv/bin/mypy glassbox/eval/reconciliation.py glassbox/sdk/tracer.py
  ```

  Expected: v2 labels the real agent's categorical urgency correctly and invalid outcome timing never reaches SQLite.

- [ ] **Step 5: Commit semantic correctness fixes.**

  ```shell
  git add glassbox/eval/reconciliation.py glassbox/eval/policies/reconciliation_v2.toml glassbox/sdk/tracer.py tests/eval/test_reconciliation.py tests/sdk/test_tracer.py
  git commit -m "fix: align reconciliation with urgency and outcome horizons"
  ```

## Task 4: Wire safe CLI and read-only planner presentation

**Files:**
- Modify: `glassbox/cli.py`
- Modify: `glassbox/web/read_service.py`
- Modify: `glassbox/web/read_models.py`
- Modify: `glassbox/web/templates/drift.html`
- Modify: `tests/test_cli.py`
- Modify: `tests/web/test_read_service.py`
- Modify: `tests/web/test_server.py`

**Interfaces:**
- `glassbox drift baseline --agent NAME --policy PATH --start YYYY-MM-DDTHH:MM:SSZ --end YYYY-MM-DDTHH:MM:SSZ [--supersede-baseline]`.
- `DriftReportView` includes `policy_hash`, `monitored_signals`, and all known signal cards, with omitted policy signals marked disabled.

- [ ] **Step 1: Write failing CLI and UI tests.**

  Add parser tests proving both `glassbox drift --policy custom.toml baseline --agent a --start 2026-09-01T00:00:00Z --end 2026-10-01T00:00:00Z` and baseline-local flag order load `custom.toml`; no child default may replace the parent value. Add tests rejecting non-Z, unordered, and future end timestamps with exit `2`; assert a valid overlapping current recent window prints a warning to stderr and persists the supplied range.

  Add a missing `--database` outcomes-import test asserting exit `2`, no database file, and the generic safe error. Add a deficient baseline test asserting stderr includes `trace_latency_ms` but excludes the SQLite path. Add read-service/template tests that show policy hash, **Not monitored (disabled by policy)** for latency/cost, `Review drift report` for drift detection, and `baseline_has_no_variance` guidance.

- [ ] **Step 2: Run tests to verify red.**

  Run:

  ```shell
  .venv/bin/pytest tests/test_cli.py tests/web/test_read_service.py tests/web/test_server.py -q
  ```

  Expected: the parser clobbers the parent policy, disabled cards do not exist, and output hides every useful deficiency.

- [ ] **Step 3: Implement parser, diagnostics, and display changes.**

  Declare drift report flags only on `drift`, and baseline-specific `--agent`, `--start`, and `--end` only on `baseline`; resolve one policy path without duplicate argument names. Parse strict timestamps with the policy parser helper, capture `created_at` once, reject `end > created_at`, and calculate overlap against `[created_at - recent_duration, created_at]` for a warning.

  Check `Path(arguments.database).is_file()` before outcomes import opens a writable database. Print only curated `DriftPolicyError`/baseline insufficiency content; retain generic error text for SQLite and filesystem failures. Make `ReadService` load `drift_v2` by default, build synthetic disabled display rows from known signal names, and render the policy hash and bounded diagnostic summary without raw CUSUM paths.

- [ ] **Step 4: Verify green.**

  Run:

  ```shell
  .venv/bin/pytest tests/test_cli.py tests/web/test_read_service.py tests/web/test_server.py -q
  .venv/bin/ruff check glassbox/cli.py glassbox/web tests/test_cli.py tests/web
  .venv/bin/mypy glassbox
  .venv/bin/lint-imports
  ```

  Expected: command behavior is safe and explicit, and the web route remains read-only.

- [ ] **Step 5: Commit operator-facing remediation.**

  ```shell
  git add glassbox/cli.py glassbox/web tests/test_cli.py tests/web
  git commit -m "fix: make drift operations explicit and diagnosable"
  ```

## Task 5: Amend counterfactual planning, documentation, and hygiene

**Files:**
- Modify: `docs/superpowers/plans/2026-10-09-glassbox-p3-counterfactual-prompts.md`
- Modify: `README.md`
- Modify: `TODO.md`
- Modify: `.gitignore` only if required to keep ignored scratch artifacts untracked
- Remove from Git tracking, preserve locally: `.superpowers/sdd/task-1-report.md`, `.superpowers/sdd/task-2-report.md`, `.superpowers/sdd/task-4-report.md`
- Modify: `tests/test_architecture.py`

- [ ] **Step 1: Write failing documentation/architecture assertions.**

  Add a lightweight text assertion that the counterfactual plan forbids original recommendation/rationale in provider input, forbids inline capture content in result persistence, and defines zero-success runs as non-zero CLI exits. Extend architecture tests only if needed to ensure the counterfactual command remains lazy-loaded outside non-counterfactual CLI branches.

- [ ] **Step 2: Run the test to verify red.**

  Run:

  ```shell
  .venv/bin/pytest tests/test_architecture.py -q
  ```

  Expected: the plan still permits the unsafe input/storage/exit behavior.

- [ ] **Step 3: Amend operator documentation and future-plan contracts.**

  Update the counterfactual plan so provider input excludes original recommendation/rationale, stored results contain capture refs and rendered-input hash rather than capture content, and reports call unmatched-provider runs prompt-and-model counterfactuals. Add a zero-success exit assertion. Update README with `drift baseline --start 2026-09-01T00:00:00Z --end 2026-10-01T00:00:00Z`, disabled-cost explanation, optional latency policy example, v2 reconciliation selection, and an explicit negative outcome JSONL record. Update TODO with cost ingestion and optional latency enablement as future operational work.

  Remove scratch reports from the Git index with `git rm --cached` only; verify the local files still exist and `.gitignore` excludes their directory.

- [ ] **Step 4: Run the release gate.**

  Run:

  ```shell
  .venv/bin/pytest -q
  .venv/bin/ruff check .
  .venv/bin/mypy glassbox
  .venv/bin/lint-imports
  git diff --check
  git status --short
  ```

  Expected: all tests pass; no database, WAL/SHM, `.env`, blob, or `.superpowers` scratch artifact is staged.

- [ ] **Step 5: Commit remediation documentation and hygiene.**

  ```shell
  git add README.md TODO.md docs/superpowers/plans/2026-10-09-glassbox-p3-counterfactual-prompts.md tests/test_architecture.py .gitignore
  git rm --cached .superpowers/sdd/task-1-report.md .superpowers/sdd/task-2-report.md .superpowers/sdd/task-4-report.md
  git commit -m "docs: clarify drift and counterfactual operations"
  ```

## Plan self-review

- **Spec coverage:** Task 1 supplies optional bounded/calibrated policy logic. Task 2 supplies atomic schema and policy-scoped outcome provenance. Task 3 corrects v2 semantics and telemetry. Task 4 delivers input-safe CLI and read-only UI behavior. Task 5 amends unimplemented counterfactual scope, documents v2 operations, and removes tracked scratch artifacts.
- **Type consistency:** Store remains policy-neutral; eval selects policy signals/rules; CLI performs argument and safe-diagnostic handling; web only consumes display models.
- **No placeholder scan:** Every behavior has a named file, public interface, concrete tests, commands, and commit boundary.

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-10-10-glassbox-p3-p4-remediation.md`.

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.
