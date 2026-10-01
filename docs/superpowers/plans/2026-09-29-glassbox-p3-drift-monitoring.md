# Glassbox P3 Drift Monitoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add policy-governed, agent-scoped drift baselines, CLI audit snapshots, and an authenticated read-only localhost report for decision and trace behavior.

**Architecture:** Keep every drift policy parser, metric, typed calculation model, and orchestration function in the single public module `glassbox.eval.drift`; it imports `glassbox.store` but never production packages. The repository owns SQLite reads, immutable baseline/run/result inserts, and active-baseline discovery. The CLI lazily imports drift only in its branch; the web layer opens only `Database.open_read_only()`, invokes the shared engine, and maps its typed result into Jinja display data without persisting a run.

**Tech Stack:** Python 3.11 standard-library `tomllib`/`hashlib`/statistics, SQLite, FastAPI, Jinja2, pytest, Ruff, mypy, import-linter.

## Global Constraints

- Keep the project Python `>=3.11`, local-first, and free of external egress; do not add runtime dependencies.
- Scope every baseline and report cohort by `agent_name` only. Preserve agent-version frequency maps for provenance; never partition the cohort by agent version.
- Use `confidence` and `decision_type` decision populations only; use `latency_ms` and `total_cost_usd` trace populations only. Never copy or divide a trace metric per decision.
- Exclude a NULL trace metric only from that metric’s population, never coerce it to zero, and apply that signal’s sample floor to its non-NULL count.
- Load the checked-in default `glassbox/eval/policies/drift_v1.toml`; accept `--policy PATH` only for CLI commands. HTTP must never accept a policy path.
- Hash canonical policy bytes with SHA-256 and persist both the non-empty `policy_version` and `policy_hash` on every baseline and CLI snapshot.
- Baselines are immutable. A duplicate active `(agent_name, policy_hash)` baseline refuses by default; `--supersede-baseline` appends a linked replacement inside `BEGIN IMMEDIATE` and never overwrites/deletes history.
- Capture one UTC `as_of` per report. Baseline and recent windows are half-open `[start, end)`; recent is `[as_of - duration, as_of)`.
- Per-signal and overall statuses are exactly `healthy`, `watch`, `drift_detected`, or `insufficient_data`; aggregate in that order: insufficient first, then detected, watch, healthy.
- A completed CLI report—including detected drift or insufficient data—exits `0`; CLI operational failures exit `2`. Baseline creation exits `0` only after inserting a baseline and otherwise exits `2`.
- `/drift` inherits the existing local session guard, performs no writes, and uses `unavailable(request)` for SQLite/read-only failures. Never expose database paths or SQLite exception text.
- Keep all drift code in `glassbox.eval.drift`, with no `glassbox.eval.drift_*` siblings. `web` may import only `glassbox.eval.drift`, while SDK/collector remain forbidden from all `glassbox.eval` imports.

---

## File structure

| Path | Responsibility |
| --- | --- |
| `glassbox/eval/policies/drift_v1.toml` | Versioned, checked-in default policy and fixed baseline window. |
| `glassbox/eval/drift.py` | The sole public drift module: TOML validation, canonical hashing, PSI/CUSUM calculations, typed reports, and CLI/live orchestration. |
| `glassbox/store/migrations/004_drift_monitoring.sql` | Immutable baseline, run, and per-signal result tables plus indexes. |
| `glassbox/store/schema.sql` | Exact current schema mirror, including the three drift tables. |
| `glassbox/store/database.py` | Applies migration 004 and fingerprints P3a and current P3b schemas correctly. |
| `glassbox/store/repository.py` | Drift source queries, agent discovery, baseline head classification, and immutable persistence. |
| `glassbox/cli.py` | Lazy `drift` command dispatch, baseline creation, JSON snapshot output, and exit codes. |
| `glassbox/web/read_models.py` | Jinja-safe drift landing/report display models. |
| `glassbox/web/read_service.py` | Per-request read-only live drift calculation and known-cohort lookup. |
| `glassbox/web/server.py` | Authenticated `/drift` route using the existing unavailable/not-found conventions. |
| `glassbox/web/templates/drift.html` | Landing list and overview-first agent drift report. |
| `glassbox/web/templates/base.html` | Adds a visible Drift navigation link. |
| `glassbox/web/static/glassbox.css` | Status badges, signal cards, and responsive distribution/CUSUM tables. |
| `tests/eval/test_drift.py` | Pure policy and calculation tests, including synthetic shifts. |
| `tests/store/test_database.py` | Migration/fingerprint/read-only schema coverage. |
| `tests/store/test_repository.py` | Drift selection, head, immutability, and snapshot persistence tests. |
| `tests/test_cli.py` | Baseline/report commands, single-`as_of`, JSON provenance, and exits. |
| `tests/web/test_read_models.py` | Display-model status/action and safe formatting tests. |
| `tests/web/test_read_service.py` | Read-only live report and known-cohort tests. |
| `tests/web/test_server.py` | Session-protected `/drift`, generic failure, and no-write HTTP tests. |
| `tests/test_architecture.py`, `pyproject.toml` | Explicit single-module drift import boundary checks. |
| `README.md`, `TODO.md` | Operator instructions and P3 completion evidence. |

## Task 1: Define the policy, typed drift contract, and pure metrics

**Files:**
- Create: `glassbox/eval/policies/drift_v1.toml`
- Create: `glassbox/eval/drift.py`
- Create: `tests/eval/test_drift.py`

**Interfaces:**
- Produces `DriftPolicy`, `SignalPolicy`, `SignalName`, `DriftStatus`, `InsufficientReason`, `DriftSignalReport`, `DriftReport`, and `load_policy(path: Path) -> DriftPolicy`.
- Produces `policy_hash(policy: DriftPolicy) -> str`, `calculate_baseline(policy: DriftPolicy, decisions: Sequence[DecisionSample], traces: Sequence[TraceSample]) -> BaselineMaterial`, and `calculate_report(policy: DriftPolicy, baseline: BaselineMaterial, recent_decisions: Sequence[DecisionSample], recent_traces: Sequence[TraceSample], as_of: datetime) -> DriftReport`.
- Consumes ordinary in-memory `DecisionSample`, `TraceSample`, and materialized baseline values; this task does not import SQLite, FastAPI, or web display types.

- [ ] **Step 1: Write failing policy and pure-metric tests.**

  Create `tests/eval/test_drift.py` with a fixed UTC policy fixture and tests for each rule below. Use minimal in-memory samples rather than a database:

  ```python
  from datetime import UTC, datetime, timedelta
  from pathlib import Path

  import pytest

  from glassbox.eval.drift import (
      DriftPolicyError,
      DecisionSample,
      TraceSample,
      calculate_report,
      load_policy,
  )


  def test_policy_hash_is_stable_for_identical_canonical_toml(tmp_path: Path) -> None:
      policy_path = tmp_path / "policy.toml"
      policy_path.write_text(POLICY_TOML)
      assert load_policy(policy_path).policy_hash == load_policy(policy_path).policy_hash


  @pytest.mark.parametrize("replacement", ["policy_version = ''", "recent_duration = '0d'"])
  def test_policy_rejects_invalid_input_before_any_data_access(tmp_path: Path, replacement: str) -> None:
      path = tmp_path / "bad.toml"
      path.write_text(POLICY_TOML.replace("policy_version = 'v1'", replacement))
      with pytest.raises(DriftPolicyError):
          load_policy(path)


  def test_confidence_and_decision_type_psi_detect_a_synthetic_shift() -> None:
      baseline_decisions = [DecisionSample("a", "v1", 0.05, "review", BASELINE_AT)] * 100
      recent = [DecisionSample("a", "v2", 0.95, "order", RECENT_AT)] * 30
      baseline = calculate_baseline(POLICY, baseline_decisions, ())
      report = calculate_report(POLICY, baseline, recent, (), AS_OF)
      assert report.signal("confidence").status == "drift_detected"
      assert report.signal("decision_type").status == "drift_detected"


  def test_cusum_detects_positive_and_negative_trace_shifts() -> None:
      baseline_traces = tuple(TraceSample("a", "v1", 10.0, 1.0, BASELINE_AT, 1) for _ in range(50))
      high = tuple(TraceSample("a", "v2", 30.0, 4.0, RECENT_AT, 1) for _ in range(20))
      low = tuple(TraceSample("a", "v2", 1.0, 0.1, RECENT_AT, 1) for _ in range(20))
      baseline = calculate_baseline(POLICY, (), baseline_traces)
      assert calculate_report(POLICY, baseline, (), high, AS_OF).signal("trace_latency_ms").status == "drift_detected"
      assert calculate_report(POLICY, baseline, (), low, AS_OF).signal("trace_cost_usd").status == "drift_detected"
  ```

  Add focused tests for: confidence bin edges `0.0`, `0.1`, `1.0`; ordered bins; decision-type `other`; smoothing with absent categories; warning/alert exact boundaries; zero CUSUM variance; each `insufficient_data` aggregation precedence branch; `watch`; and immutable version-frequency/decisions-per-trace diagnostics.

- [ ] **Step 2: Run the tests to confirm the module is absent.**

  Run: `pytest tests/eval/test_drift.py -q`

  Expected: collection fails with `ModuleNotFoundError: No module named 'glassbox.eval.drift'`.

- [ ] **Step 3: Add the exact checked-in policy.**

  Create `glassbox/eval/policies/drift_v1.toml` with these fields and values (use a real historical UTC interval that ends before the project’s current seed fixtures; tests may use copied temporary policies):

  ```toml
  policy_version = "drift_v1"
  baseline_start = "2026-01-01T00:00:00Z"
  baseline_end = "2026-06-01T00:00:00Z"
  recent_duration = "7d"

  [confidence]
  population = "decision"
  algorithm = "psi"
  minimum_baseline_samples = 100
  minimum_recent_samples = 30
  bin_boundaries = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
  warning_threshold = 0.10
  alert_threshold = 0.20

  [decision_type]
  population = "decision"
  algorithm = "psi"
  minimum_baseline_samples = 100
  minimum_recent_samples = 30
  smoothing = 0.0001
  warning_threshold = 0.10
  alert_threshold = 0.20

  [trace_latency_ms]
  population = "trace"
  algorithm = "cusum"
  minimum_baseline_samples = 50
  minimum_recent_samples = 20
  reference_value = 0.5
  warning_threshold = 3.0
  alert_threshold = 5.0

  [trace_cost_usd]
  population = "trace"
  algorithm = "cusum"
  minimum_baseline_samples = 50
  minimum_recent_samples = 20
  reference_value = 0.5
  warning_threshold = 3.0
  alert_threshold = 5.0
  ```

- [ ] **Step 4: Implement the single drift module with validation and deterministic math.**

  In `glassbox/eval/drift.py`, use only stdlib imports plus `glassbox.store` types. Define all names consumed later in this plan:

  ```python
  SignalName: TypeAlias = Literal[
      "confidence", "decision_type", "trace_latency_ms", "trace_cost_usd"
  ]
  DriftStatus: TypeAlias = Literal["healthy", "watch", "drift_detected", "insufficient_data"]
  InsufficientReason: TypeAlias = Literal[
      "baseline_not_created", "policy_changed_requires_rebaseline",
      "baseline_window_too_small", "recent_window_too_small",
  ]

  @dataclass(frozen=True)
  class DecisionSample:
      agent_name: str
      agent_version: str
      confidence: float
      decision_type: str
      decided_at: datetime

  @dataclass(frozen=True)
  class TraceSample:
      agent_name: str
      agent_version: str
      latency_ms: float | None
      total_cost_usd: float | None
      started_at: datetime
      decision_count: int

  def load_policy(path: Path) -> DriftPolicy:
      """Parse and validate the complete TOML policy before database access."""

  def calculate_baseline(
      policy: DriftPolicy, decisions: Sequence[DecisionSample], traces: Sequence[TraceSample]
  ) -> BaselineMaterial:
      """Materialize only the distributions/statistics needed for future comparisons."""

  def calculate_report(
      policy: DriftPolicy, baseline: BaselineMaterial,
      recent_decisions: Sequence[DecisionSample], recent_traces: Sequence[TraceSample],
      as_of: datetime,
  ) -> DriftReport:
      """Calculate all four signal reports against an immutable baseline."""
  ```

  Parse TOML with `tomllib.loads(path.read_text(encoding="utf-8"))`. Canonicalize the parsed, validated policy with `json.dumps(policy_mapping, sort_keys=True, separators=(",", ":"))`, then hash its UTF-8 bytes with `hashlib.sha256`; retain the source `policy_version` separately. Reject unknown/missing signals, invalid UTC timestamp text, unordered bounds, non-finite floats, non-positive counts/duration, invalid bin endpoints, and unordered warning/alert limits with `DriftPolicyError` before accepting any source samples.

  Implement PSI as `sum((recent - baseline) * log(recent / baseline))` over aligned bins/categories. Confidence bins include their lower edge and only the final bin includes `1.0`. Decision-type categories equal baseline categories plus `other`; apply the configured positive smoothing to both distributions before normalizing. Materialize baseline counts/proportions and trace mean/sample standard deviation, not source events. For CUSUM use standardized residuals `(value - mean) / standard_deviation`, update `positive = max(0, positive + residual - reference_value)` and `negative = min(0, negative + residual + reference_value)`, and use `max(positive, abs(negative))` as the metric. A zero standard deviation is an `insufficient_data` result, not infinity.

  Make `DriftReport.to_dict()` JSON-safe and include: `status`, `reason`, `as_of`, both windows, policy version/hash, agent name, baseline ID when present, version maps, decisions-per-trace summary, and four signal objects with population/algorithm/counts/value/thresholds/status/details. `DriftReport.signal(name)` must return the named report or raise `KeyError`.

- [ ] **Step 5: Run the pure test cycle and static checks.**

  Run:

  ```shell
  pytest tests/eval/test_drift.py -q
  ruff check glassbox/eval/drift.py tests/eval/test_drift.py
  mypy glassbox/eval/drift.py
  ```

  Expected: all pass. Confirm a test whose recent confidence changes from `0.05` to `0.95` is `drift_detected`, while its baseline object remains unchanged after report calculation.

- [ ] **Step 6: Commit the pure, independently reviewable drift core.**

  ```shell
  git add glassbox/eval/drift.py glassbox/eval/policies/drift_v1.toml tests/eval/test_drift.py
  git commit -m "feat: add drift policy and metrics"
  ```

## Task 2: Add strict immutable drift persistence and source queries

**Files:**
- Create: `glassbox/store/migrations/004_drift_monitoring.sql`
- Modify: `glassbox/store/schema.sql`
- Modify: `glassbox/store/database.py`
- Modify: `glassbox/store/repository.py`
- Modify: `tests/store/test_database.py`
- Modify: `tests/store/test_repository.py`
- Modify: `tests/store/test_schema.py`

**Interfaces:**
- Produces repository dataclasses `DriftBaselineRecord`, `DriftRunRecord`, `DriftResultRecord`, `DriftSourceData`, and `DriftBaselineConflictError`.
- Produces `Repository.drift_agent_names()`, `drift_source_data(agent_name, baseline_start, baseline_end, recent_start, recent_end)`, `active_drift_baseline(agent_name, policy_hash)`, `record_drift_baseline(request, supersede=False)`, and `record_drift_run(run, results)`.
- Consumes serializable baseline/report values from `glassbox.eval.drift` but does not import that module; caller supplies plain JSON-safe mappings and validated dataclass fields.

- [ ] **Step 1: Write failing migration and repository tests.**

  Add tests that seed two agents, multiple decisions in one trace, null trace latency/cost, and two agent versions. Assert:

  ```python
  def test_drift_source_data_keeps_decisions_and_traces_as_separate_populations(
      seeded_drift_repository: Repository,
  ) -> None:
      source = seeded_drift_repository.drift_source_data(
          "agent-a", BASELINE_START, BASELINE_END, RECENT_START, AS_OF
      )
      assert len(source.baseline_decisions) == 3
      assert len(source.baseline_traces) == 1
      assert source.baseline_traces[0].decision_count == 3
      assert source.baseline_traces[0].latency_ms is None

  def test_record_drift_baseline_refuses_duplicate_head_and_supersedes_append_only(
      seeded_drift_repository: Repository,
  ) -> None:
      first = seeded_drift_repository.record_drift_baseline(first_request)
      with pytest.raises(DriftBaselineConflictError):
          seeded_drift_repository.record_drift_baseline(first_request)
      second = seeded_drift_repository.record_drift_baseline(second_request, supersede=True)
      assert second.supersedes_baseline_id == first.baseline_id
      assert seeded_drift_repository.active_drift_baseline("agent-a", POLICY_HASH) == second

  def test_drift_run_references_exact_immutable_baseline(
      seeded_drift_repository: Repository,
  ) -> None:
      baseline = seeded_drift_repository.record_drift_baseline(request)
      run = seeded_drift_repository.record_drift_run(run_request, result_requests)
      assert run.baseline_id == baseline.baseline_id
      assert seeded_drift_repository._connection.execute(
          "SELECT count(*) FROM drift_results"
      ).fetchone()[0] == 4
  ```

  Include tests for: active-head none/one/multiple/zero-head-with-rows classification, `BEGIN IMMEDIATE` rollback on invalid insert, agent-name-only selection, UTC half-open bounds, raw timestamp precision correctness through `glassbox_timestamp_key`, NULL metric counts, and `ON DELETE RESTRICT` relationships.

- [ ] **Step 2: Run targeted tests to verify the persistence API is absent.**

  Run: `pytest tests/store/test_database.py tests/store/test_repository.py -q -k drift`

  Expected: collection/import failures for the new repository symbols.

- [ ] **Step 3: Add migration 004 and mirror it exactly in the schema.**

  Create three tables with strict ULID primary keys, UTC timestamps using the project’s existing timestamp CHECK expression, JSON validity checks, and these relations:

  ```sql
  CREATE TABLE IF NOT EXISTS drift_baselines (
      baseline_id TEXT PRIMARY KEY NOT NULL CHECK (length(baseline_id) = 26 AND substr(baseline_id, 1, 1) GLOB '[0-7]' AND baseline_id NOT GLOB '*[^0123456789ABCDEFGHJKMNPQRSTVWXYZ]*'),
      agent_name TEXT NOT NULL,
      policy_version TEXT NOT NULL,
      policy_hash TEXT NOT NULL,
      baseline_start TEXT NOT NULL CHECK (date(baseline_start) = substr(baseline_start, 1, 10)),
      baseline_end TEXT NOT NULL CHECK (date(baseline_end) = substr(baseline_end, 1, 10)),
      created_at TEXT NOT NULL CHECK (date(created_at) = substr(created_at, 1, 10)),
      version_counts_json TEXT NOT NULL CHECK (json_valid(version_counts_json)),
      reference_json TEXT NOT NULL CHECK (json_valid(reference_json)),
      supersedes_baseline_id TEXT REFERENCES drift_baselines(baseline_id) ON DELETE RESTRICT,
      CHECK (length(trim(agent_name)) > 0),
      CHECK (length(trim(policy_version)) > 0),
      CHECK (length(policy_hash) = 64)
  );
  CREATE INDEX IF NOT EXISTS idx_drift_baselines_agent_policy ON drift_baselines(agent_name, policy_hash);

  CREATE TABLE IF NOT EXISTS drift_runs (
      drift_run_id TEXT PRIMARY KEY NOT NULL CHECK (length(drift_run_id) = 26 AND substr(drift_run_id, 1, 1) GLOB '[0-7]' AND drift_run_id NOT GLOB '*[^0123456789ABCDEFGHJKMNPQRSTVWXYZ]*'),
      baseline_id TEXT NOT NULL REFERENCES drift_baselines(baseline_id) ON DELETE RESTRICT,
      agent_name TEXT NOT NULL,
      policy_version TEXT NOT NULL,
      policy_hash TEXT NOT NULL,
      as_of TEXT NOT NULL CHECK (date(as_of) = substr(as_of, 1, 10)),
      recent_start TEXT NOT NULL CHECK (date(recent_start) = substr(recent_start, 1, 10)),
      recent_end TEXT NOT NULL CHECK (date(recent_end) = substr(recent_end, 1, 10)),
      status TEXT NOT NULL CHECK (status IN ('healthy','watch','drift_detected','insufficient_data')),
      status_reason TEXT,
      version_counts_json TEXT NOT NULL CHECK (json_valid(version_counts_json)),
      context_json TEXT NOT NULL CHECK (json_valid(context_json))
  );
  CREATE INDEX IF NOT EXISTS idx_drift_runs_agent_created ON drift_runs(agent_name, as_of);

  CREATE TABLE IF NOT EXISTS drift_results (
      drift_result_id TEXT PRIMARY KEY NOT NULL CHECK (length(drift_result_id) = 26 AND substr(drift_result_id, 1, 1) GLOB '[0-7]' AND drift_result_id NOT GLOB '*[^0123456789ABCDEFGHJKMNPQRSTVWXYZ]*'),
      drift_run_id TEXT NOT NULL REFERENCES drift_runs(drift_run_id) ON DELETE CASCADE,
      signal_name TEXT NOT NULL CHECK (signal_name IN ('confidence','decision_type','trace_latency_ms','trace_cost_usd')),
      population TEXT NOT NULL CHECK (population IN ('decision','trace')),
      algorithm TEXT NOT NULL CHECK (algorithm IN ('psi','cusum')),
      status TEXT NOT NULL CHECK (status IN ('healthy','watch','drift_detected','insufficient_data')),
      baseline_count INTEGER NOT NULL CHECK (baseline_count >= 0),
      recent_count INTEGER NOT NULL CHECK (recent_count >= 0),
      metric_value REAL,
      warning_threshold REAL NOT NULL,
      alert_threshold REAL NOT NULL,
      details_json TEXT NOT NULL CHECK (json_valid(details_json)),
      UNIQUE(drift_run_id, signal_name)
  );
  ```

  Replace each shortened `date(column) = substr(column, 1, 10)` timestamp line above with the exact full strict UTC RFC3339 check already used for `eval_runs.run_at`; do not weaken it to a `datetime()` check. Append the same statements to `glassbox/store/schema.sql` in the same order. Update `tests/store/test_schema.py` to byte-compare the concatenation of `001_initial.sql`, `002_feedback.sql`, `003_judge_calibration.sql`, and `004_drift_monitoring.sql` against `schema.sql`.

- [ ] **Step 4: Teach `Database` about the new current schema and safe upgrade path.**

  In `glassbox/store/database.py`:

  ```python
  _BASE_INDEXES = (
      "idx_decisions_agent_decided_at", "idx_decisions_entity", "idx_decisions_type_confidence",
  )
  _DRIFT_TABLES = ("drift_baselines", "drift_runs", "drift_results")
  _DRIFT_INDEXES = ("idx_drift_baselines_agent_policy", "idx_drift_runs_agent_created")
  _TABLES = _BASE_TABLES + ("feedback",) + _DRIFT_TABLES
  _INDEXES = _BASE_INDEXES + _DRIFT_INDEXES
  ```

  Add `_released_p3a_schema_objects()` for migration 003 and make `_current_schema_objects()` union it with exact objects from `004_drift_monitoring.sql`. In `_initialize_schema`, execute migration 004 after migration 003 for a fresh database and every older accepted strict version; for an exact P3a schema execute only 004; retain no-schema mutation in `open_read_only`. The migration creates only new tables/indexes, so do not rebuild feedback/eval tables again.

- [ ] **Step 5: Implement repository source reads and immutable writes.**

  Add persistence-only dataclasses in `glassbox/store/repository.py` with primitive/mapping fields. Keep JSON encoding via `canonical_dumps` and decoding through the existing `_json` helpers. Add these methods:

  ```python
  def drift_agent_names(self) -> tuple[str, ...]:
      """List every known decision or trace agent in stable order."""
  def drift_source_data(
      self, agent_name: str, baseline_start: datetime, baseline_end: datetime,
      recent_start: datetime, recent_end: datetime,
  ) -> DriftSourceData:
      """Load the independent decision and trace populations for both windows."""
  def drift_baseline_state(self, agent_name: str, policy_hash: str) -> DriftBaselineState:
      """Classify exact-policy baseline history as absent, active, or malformed."""
  def record_drift_baseline(
      self, request: DriftBaselineInsert, *, supersede: bool = False,
  ) -> DriftBaselineRecord:
      """Append an immutable baseline or raise DriftBaselineConflictError."""
  def record_drift_run(
      self, run: DriftRunInsert, results: tuple[DriftResultInsert, ...],
  ) -> DriftRunRecord:
      """Append one immutable run and its four signal results."""
  ```

  `drift_agent_names()` must union distinct names from `decisions` and `traces`, sorted lexicographically. `drift_source_data()` must use bound `agent_name` and `glassbox_timestamp_key(...)` predicates to make both windows half-open. Query decisions independently from traces. Query trace `decision_count` with a correlated/count subquery or grouped left join; retain traces with NULL cost/latency so each signal can filter it later.

  `record_drift_baseline` must acquire the repository lock, execute `BEGIN IMMEDIATE`, compute heads using the same “row not referenced by another row’s `supersedes_baseline_id`” rule as overrides, then: insert `NULL` predecessor if no rows; refuse a malformed zero/multiple-head history; refuse a single head unless `supersede`; or insert a new row referencing the one head. Commit only after inserting the immutable record; roll back every failure. Validate non-empty agent/version/hash, ordered bounds, four reference signals, and valid JSON before SQL.

- [ ] **Step 6: Verify migrations, persistence, read-only behavior, and full repository suite.**

  Run:

  ```shell
  pytest tests/store/test_schema.py tests/store/test_database.py tests/store/test_repository.py -q
  ruff check glassbox/store tests/store
  mypy glassbox/store
  ```

  Expected: a P3a database upgrades once, a P3b database opens read-only, and no `open_read_only()` call creates a drift table or file.

- [ ] **Step 7: Commit storage as a standalone migration/persistence increment.**

  ```shell
  git add glassbox/store/migrations/004_drift_monitoring.sql glassbox/store/schema.sql glassbox/store/database.py glassbox/store/repository.py tests/store
  git commit -m "feat: persist immutable drift baselines and runs"
  ```

## Task 3: Connect the shared engine to baseline and report CLI commands

**Files:**
- Modify: `glassbox/eval/drift.py`
- Modify: `glassbox/cli.py`
- Modify: `tests/eval/test_drift.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces `create_baseline(database_path: Path, agent_name: str, policy: DriftPolicy, *, supersede: bool, clock: Callable[[], datetime]) -> DriftBaselineRecord`.
- Produces `run_drift_report(database_path: Path, agent_name: str, policy: DriftPolicy, *, clock: Callable[[], datetime], persist: bool) -> DriftReport`.
- CLI syntax: `glassbox drift baseline --agent NAME [--policy PATH] [--supersede-baseline]` and `glassbox drift --agent NAME [--policy PATH]`.

- [ ] **Step 1: Write failing orchestration and CLI tests.**

  Add a clock injection test that proves a single run uses the same `as_of` in output, recent end, persistence, and source query:

  ```python
  def test_run_drift_report_captures_as_of_once_and_persists_exact_snapshot(
      tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      report = run_drift_report(path, "agent-a", policy, clock=lambda: AS_OF, persist=True)
      assert report.as_of == AS_OF
      stored = repository.latest_drift_run("agent-a")
      assert stored.as_of == AS_OF
      assert stored.recent_end == AS_OF

  def test_drift_cli_detected_status_is_json_and_exit_zero(
      tmp_path: Path, capsys: pytest.CaptureFixture[str],
  ) -> None:
      assert main(["--database", str(path), "drift", "--agent", "agent-a"]) == 0
      payload = json.loads(capsys.readouterr().out)
      assert payload["status"] == "drift_detected"

  def test_drift_cli_operational_failure_exits_two(tmp_path: Path) -> None:
      assert main(["--database", str(missing), "drift", "--agent", "agent-a"]) == 2
  ```

  Include exact tests for baseline insufficient samples/duplicate no supersede → `2`, valid baseline → `0`, no baseline → a persisted-or-output `insufficient_data/baseline_not_created` report with `0`, policy hash mismatch → `policy_changed_requires_rebaseline`, and `--policy` overriding the default only in CLI.

- [ ] **Step 2: Run focused tests to confirm orchestration and parser branches are absent.**

  Run: `pytest tests/eval/test_drift.py tests/test_cli.py -q -k drift`

  Expected: failures for undefined orchestration functions and unrecognized `drift` arguments.

- [ ] **Step 3: Implement engine/repository orchestration without duplicate calculations.**

  In `glassbox.eval.drift`, open `Database.open()` only in `create_baseline` and persisted `run_drift_report`; always close it in `finally`. For a baseline, fetch the exact policy historical interval, call `calculate_baseline`, refuse any signal below its baseline floor with a signal-named `DriftPolicyError`, then persist its JSON-safe material through `record_drift_baseline`.

  For a report, capture `as_of = clock()` once at function entry, derive `recent_start` once, query `drift_baseline_state` first, and return an in-memory `insufficient_data` report for no matching baseline or policy mismatch. For a matching baseline, query only required source rows, calculate once, and—when `persist=True`—write exactly one run and exactly four results whose `baseline_id`, policy provenance, and window values equal the report values. Do not write when `persist=False`.

- [ ] **Step 4: Add lazy CLI parsing and exact exit behavior.**

  In `glassbox/cli.py`, create a `drift` parser that treats no nested command as the approved report command and accepts `baseline` as its one optional nested command:

  ```python
  drift_command = commands.add_parser("drift", help="create or inspect agent drift reports")
  drift_command.add_argument("--agent")
  drift_command.add_argument("--policy", type=Path)
  drift_subcommands = drift_command.add_subparsers(dest="drift_command")
  baseline_command = drift_subcommands.add_parser("baseline", help="materialize an immutable baseline")
  baseline_command.add_argument("--agent", required=True)
  baseline_command.add_argument("--policy", type=Path)
  baseline_command.add_argument("--supersede-baseline", action="store_true")
  ```

  Require `arguments.agent` when `arguments.drift_command is None`; that exact branch implements `glassbox drift --agent NAME [--policy PATH]`. When `arguments.drift_command == "baseline"`, use the nested parser’s required agent/policy values. Import `DriftPolicyError`, `create_baseline`, `default_policy_path`, `load_policy`, and `run_drift_report` inside `if arguments.command == "drift":` only. Print compact sorted JSON from `report.to_dict()` to stdout. Catch policy validation, baseline conflicts/insufficient data, `OSError`, SQLite, and migration errors; print the generic `glassbox: unable to run drift command` message to stderr and return `2`. Do not treat `drift_detected` as an error.

- [ ] **Step 5: Run the CLI/engine test cycle.**

  Run:

  ```shell
  pytest tests/eval/test_drift.py tests/test_cli.py -q
  ruff check glassbox/cli.py glassbox/eval/drift.py tests/test_cli.py tests/eval/test_drift.py
  mypy glassbox/cli.py glassbox/eval/drift.py
  ```

  Expected: all pass; invoking `glassbox trace`, `serve`, `export`, `eval`, and `judge` still does not import `glassbox.eval.drift` at module load time.

- [ ] **Step 6: Commit the operator-facing CLI increment.**

  ```shell
  git add glassbox/cli.py glassbox/eval/drift.py tests/eval/test_drift.py tests/test_cli.py
  git commit -m "feat: add drift baseline and report commands"
  ```

## Task 4: Add authenticated live drift reporting without database writes

**Files:**
- Modify: `glassbox/eval/drift.py`
- Modify: `glassbox/web/read_models.py`
- Modify: `glassbox/web/read_service.py`
- Modify: `glassbox/web/server.py`
- Modify: `glassbox/web/templates/base.html`
- Create: `glassbox/web/templates/drift.html`
- Modify: `glassbox/web/static/glassbox.css`
- Modify: `tests/web/test_read_models.py`
- Modify: `tests/web/test_read_service.py`
- Modify: `tests/web/test_server.py`

**Interfaces:**
- Produces `ReadService.drift_agents() -> tuple[str, ...]` and `ReadService.drift_report(agent_name: str, *, clock: Callable[[], datetime] = utc_now) -> DriftPageData | None`.
- Produces display types `DriftLandingView`, `DriftReportView`, and `DriftSignalView` that contain strings/numbers/tuples only, never raw SQLite rows or policy paths.
- Adds `GET /drift` and `GET /drift?agent=NAME`; both require the existing session middleware.

- [ ] **Step 1: Write failing read-model, service, and HTTP tests.**

  Add tests covering the full route path:

  ```python
  def test_drift_requires_the_existing_login_session(client: TestClient) -> None:
      assert client.get("/drift", follow_redirects=False).headers["location"] == "/login"

  def test_live_drift_lists_known_agents_without_baselines_and_writes_nothing(
      logged_in_client: TestClient, path: Path,
  ) -> None:
      before = table_counts(path, "drift_baselines", "drift_runs", "drift_results")
      response = logged_in_client.get("/drift")
      assert "agent-with-decisions" in response.text
      assert "agent-with-only-traces" in response.text
      assert table_counts(path, "drift_baselines", "drift_runs", "drift_results") == before

  def test_live_drift_renders_actionable_policy_mismatch(logged_in_client: TestClient) -> None:
      response = logged_in_client.get("/drift?agent=agent-a")
      assert "Materialize a baseline for the changed policy" in response.text

  def test_unknown_drift_agent_is_generic_not_found(logged_in_client: TestClient) -> None:
      assert logged_in_client.get("/drift?agent=no-such-agent").status_code == 404
  ```

  Add a `ReadService` test monkeypatching `Database.open` to raise if called and recording `Database.open_read_only`; it must prove an agent report opens a fresh read-only connection and creates neither a DB nor drift rows. Test `baseline_not_created`, `policy_changed_requires_rebaseline`, baseline/recent sample-shortage action text, all four signal statuses, trace-level labels, non-null sample counts, and HTML autoescaping of agent names/version strings.

- [ ] **Step 2: Run the focused tests and confirm the route/service is absent.**

  Run: `pytest tests/web/test_read_models.py tests/web/test_read_service.py tests/web/test_server.py -q -k drift`

  Expected: failures because `ReadService` has no drift methods and `/drift` returns a generic not-found response.

- [ ] **Step 3: Add read-only orchestration and Jinja-safe display models.**

  Add `run_live_drift_report(repository: Repository, agent_name: str, policy: DriftPolicy, *, as_of: datetime) -> DriftReport` to `glassbox.eval.drift`; it must consume the repository passed by the web layer, never open a database, and never call any record method. In `ReadService`, load only `default_policy_path()`, call `Database.open_read_only`, use `Repository.drift_agent_names()` to validate the bound agent name, and close the database in `finally`.

  In `read_models.py`, create display values rather than exposing calculation objects:

  ```python
  @dataclass(frozen=True)
  class DriftSignalView:
      name: str
      population_label: str
      algorithm: str
      status: str
      metric: str
      thresholds: str
      baseline_count: int
      recent_count: int
      detail_rows: tuple[tuple[str, str, str], ...]

  @dataclass(frozen=True)
  class DriftReportView:
      agent_name: str
      status: str
      action: str
      calculated_at: str
      policy_version: str
      policy_hash: str
      baseline_id: str | None
      baseline_range: str
      recent_range: str
      baseline_versions: tuple[AttributeView, ...]
      recent_versions: tuple[AttributeView, ...]
      decisions_per_trace: str
      signals: tuple[DriftSignalView, ...]
  ```

  Map every insufficient reason to its exact approved operator action; format numbers deterministically but do not hide NULL exclusions—show each signal’s `baseline_count` and `recent_count` directly.

- [ ] **Step 4: Implement the protected route and template.**

  Add the `Drift` navigation link to `base.html`. In `server.py`, add `@app.get("/drift")` after the root route. With no `agent`, render `drift.html` with `landing=DriftLandingView(agents=service().drift_agents())` and no report. With an agent, call `service().drift_report(agent, clock=clock)` and render its report. Catch `ValueError` for malformed/missing policy data as a generic bad request only when the request is invalid; catch `(ReadOnlyDatabaseError, sqlite3.Error)` and call the existing `unavailable(request)` helper; render the existing generic `error.html` 404 for unknown cohorts.

  Create `drift.html` using `base.html`. It must show the literal label `Calculated now` for live reports; show status/action first; then cohort/policy/baseline/ranges/version mixes; then four cards; finally a baseline-vs-recent table for PSI or CUSUM path table for CUSUM. Do not add `<form>`, script, external asset, policy-path query, or POST route.

- [ ] **Step 5: Add focused styling without changing existing screens.**

  In `glassbox.css`, reuse existing palette/type tokens. Add `.status-healthy`, `.status-watch`, `.status-drift-detected`, `.status-insufficient-data`, `.signal-grid`, `.signal-card`, and responsive `.drift-detail` rules. Keep semantic text in the template so status remains understandable without CSS.

- [ ] **Step 6: Run browser-level and static checks.**

  Run:

  ```shell
  pytest tests/web/test_read_models.py tests/web/test_read_service.py tests/web/test_server.py -q
  ruff check glassbox/web tests/web
  mypy glassbox/web
  ```

  Expected: unauthenticated `/drift` redirects to login; authenticated live reads do not create `drift_runs`; an injected read-only failure renders the existing 503 page with no SQLite error text.

- [ ] **Step 7: Commit the read-only dashboard.**

  ```shell
  git add glassbox/eval/drift.py glassbox/web tests/web
  git commit -m "feat: add live drift report"
  ```

## Task 5: Enforce drift boundaries, document operation, and run the release gate

**Files:**
- Modify: `pyproject.toml`
- Modify: `tests/test_architecture.py`
- Modify: `README.md`
- Modify: `TODO.md`
- Modify: relevant tests only if the full verification uncovers a real gap

**Interfaces:**
- Web imports `glassbox.eval.drift` and no `glassbox.eval.drift_*`/`glassbox.eval.drift.` name.
- CLI drift imports happen only in the drift branch; all other command branches remain free of the module at import time.

- [ ] **Step 1: Write failing architectural and documentation assertions.**

  Extend the existing `web-dependencies` expected forbidden set in `tests/test_architecture.py` with no broad `glassbox.eval` prohibition; instead add an AST test:

  ```python
  def test_web_uses_only_the_single_public_drift_module() -> None:
      imports = _all_absolute_import_names(PROJECT_ROOT / "glassbox" / "web")
      drift_imports = {name for name in imports if name.startswith("glassbox.eval.drift")}
      assert drift_imports <= {"glassbox.eval.drift"}
  ```

  Add a mutation fixture/source assertion that a future `from glassbox.eval.drift_policy import load_policy` fails this test. Add a CLI AST test that `glassbox.eval.drift` is not a module-level import in `glassbox/cli.py`.

- [ ] **Step 2: Run architecture tests and confirm the new guard fails before implementation.**

  Run: `pytest tests/test_architecture.py -q`

  Expected: the new assertions fail until the helper/contract expectation accurately captures the actual drift import path.

- [ ] **Step 3: Implement exact boundary enforcement.**

  Retain the existing explicit judge sibling entries. Add `glassbox.eval.drift` to the `web-dependencies` **allowed-by-architecture test** rather than its forbidden list; the module is intentionally allowed. Ensure `sdk-dependencies` and `collector-dependencies` continue to forbid all `glassbox.eval`, which includes drift. Keep `eval-dependencies` forbidding web/sdk/collector. Update `test_import_linter_actually_evaluates_and_enforces_the_configured_contracts()` only if a real contract count changes; this feature should not add a seventh contract.

- [ ] **Step 4: Document exact operator flow and completion status.**

  In `README.md`, add a short Drift monitoring section with:

  ```shell
  glassbox drift baseline --agent replenishment-triage-ai
  glassbox drift --agent replenishment-triage-ai
  glassbox serve
  # visit http://127.0.0.1:8787/drift after local login
  ```

  Explain that policy lives in `glassbox/eval/policies/drift_v1.toml`, `--policy` is CLI-only, baselines are immutable/superseded explicitly, reports are informational, and the live UI is calculated now/read-only while CLI reports are audit snapshots. In `TODO.md`, check only P3 drift items substantiated by this work; retain counterfactuals, Discord, scheduling, CI gating, and planner usability work as deferred/open.

- [ ] **Step 5: Run the full release verification.**

  Run:

  ```shell
  pytest -q
  ruff check .
  mypy glassbox
  lint-imports
  git diff --check
  ```

  Expected: all pass. Inspect `git status --short` and ensure no generated SQLite database, `.env`, WAL/SHM sidecar, or `.superpowers/brainstorm/` content is staged.

- [ ] **Step 6: Perform a manual local smoke test with synthetic data.**

  Seed a temporary database through existing event models, create a baseline under a temporary copied policy with low fixture floors, then run:

  ```shell
  glassbox --database /tmp/glassbox-drift-smoke.sqlite3 drift baseline --agent agent-a --policy /tmp/drift-smoke.toml
  glassbox --database /tmp/glassbox-drift-smoke.sqlite3 drift --agent agent-a --policy /tmp/drift-smoke.toml
  GLASSBOX_LOCAL_ACCESS_TOKEN='12345678901234567890123456789012' GLASSBOX_DATABASE=/tmp/glassbox-drift-smoke.sqlite3 glassbox serve
  ```

  Confirm the CLI emits JSON with a policy hash/baseline ID and status, and the authenticated `/drift?agent=agent-a` screen says `Calculated now` without adding a `drift_runs` row.

- [ ] **Step 7: Commit docs and safeguards.**

  ```shell
  git add pyproject.toml tests/test_architecture.py README.md TODO.md
  git commit -m "docs: document drift monitoring operations"
  ```

## Plan self-review

- **Spec coverage:** Task 1 covers TOML policy validation/hash, all four algorithms, immutable mathematical material, threshold/zero-variance/status branches, and synthetic shifts. Task 2 covers strict migration/fingerprints, agent-only cohorts, trace/decision separation, null handling, immutable baseline head/supersession, and snapshot provenance. Task 3 covers exact CLI syntax, captured `as_of`, persistence, JSON, and exits. Task 4 covers session-protected read-only UI, known cohort discovery, all insufficient reasons, presentation and generic errors. Task 5 covers boundaries, docs, complete verification, and manual smoke checks.
- **Placeholder scan:** The plan contains no deferred implementation placeholders; every created interface, migration table, test category, command, and error behavior is named above.
- **Type consistency:** `DriftPolicy`/`DriftReport` are public only from `glassbox.eval.drift`; repository persistence records are store-owned primitives to preserve `store → eval` prohibition. CLI and web both call the same report engine, with `persist=True` only for CLI and an injected read-only repository only for web.
