# Glassbox P4 Outcome Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add idempotent JSONL outcome ingestion and policy-scoped deferred-truth reconciliation reporting for recorded Glassbox decisions.

**Architecture:** A single `glassbox.eval.reconciliation` module owns checked-in TOML policy validation, JSON-pointer interpretation, JSONL/reject-file orchestration, and immutable report calculation. `glassbox.store` owns the migration, typed SQLite rows, replay-safe outcome writes, and report source queries without importing eval. `glassbox.cli` lazily imports reconciliation only in the `outcomes` command branch.

**Tech Stack:** Python 3.11+, stdlib `tomllib`/`json`/`hashlib`, SQLite, Pydantic event records, pytest, Ruff, mypy, import-linter.

## Global Constraints

- Inputs are UTF-8 JSONL; blank lines are ignored and every non-blank line is one JSON object with `source_id`, `decision_id`, `outcome_type`, `observed_at`, `horizon_days`, and `value`.
- Only an explicit persisted `decision_id` associates an outcome with a decision; P4 performs no entity, SKU, or timestamp matching.
- `source_id` is source-owned, non-empty, and idempotent: an exact replay is a no-op; any payload conflict is a rejected line with no write.
- Import accepted rows independently; write all rejected rows to a required JSONL `--rejects` artifact atomically with one-based source line numbers and safe reason codes.
- The checked-in default policy is `glassbox/eval/policies/reconciliation_v1.toml`; `--policy PATH` is CLI-only.
- A policy has a SHA-256 canonical hash, a non-empty version, a positive 30-day maturity window, and exactly one applicable rule per accepted decision/outcome pair.
- Rules map opaque recommendation and outcome JSON through RFC 6901-style object-key JSON pointers; pointer targets must be scalar. No agent-specific source code or inferred action semantics are allowed.
- `tp`/`fp`/`tn`/`fn` are derived from configured positive prediction and positive outcome values, then persisted with policy version/hash.
- Reports use one captured UTC `as_of`, include only decisions with `decided_at <= as_of - maturity_days`, and never count younger decisions as missing outcomes.
- Report aggregates use only outcomes carrying the requested policy hash; a policy revision is a distinct cohort.
- When several compatible outcomes exist for one decision/outcome type, choose the newest parsed `observed_at`, then `outcome_id` descending; one decision contributes at most one label.
- Empty precision/recall denominators serialize as JSON `null`, never `0`.
- CLI exits: import `0` (no rejections), `1` (completed with rejected lines), `2` (operational failure); report `0` (completed), `2` (operational failure).
- Do not add HTTP/UI, provider egress, scheduling, alerts, CI enforcement, business-key matching, or an SDK outcome API in P4.
- `sdk`, `collector`, and `web` remain forbidden from all `glassbox.eval`; CLI reconciliation imports must remain inside the `outcomes` branch.

---

## File structure

| Path | Responsibility |
| --- | --- |
| `glassbox/eval/policies/reconciliation_v1.toml` | Versioned, checked-in 30-day rule mapping example. |
| `glassbox/eval/reconciliation.py` | Policy loading/hash, strict JSON pointer/rule evaluation, JSONL import orchestration, reject writing, and report calculation. |
| `glassbox/store/migrations/005_outcome_reconciliation.sql` | Atomic rebuild of the P0 outcomes table with nullable legacy provenance and partial unique source-ID index. |
| `glassbox/store/schema.sql` | Exact released-schema documentation mirror. |
| `glassbox/store/database.py` | P3b-to-P4 migration and strict current-schema fingerprint. |
| `glassbox/store/repository.py` | Outcome submission/replay persistence and policy-scoped reconciliation source data. |
| `glassbox/cli.py` | Lazy `outcomes import` and `outcomes report` dispatch with compact JSON output. |
| `tests/eval/test_reconciliation.py` | Policy/pointer, importer, reject artifact, and report calculations. |
| `tests/store/test_database.py`, `tests/store/test_repository.py`, `tests/store/test_schema.py` | Migration, legacy preservation, replay, ordering, and strict schema tests. |
| `tests/test_cli.py`, `tests/test_architecture.py` | Exit behavior and lazy import boundary tests. |
| `README.md`, `TODO.md` | Operator flow and P4 completion/deferred scope. |

## Task 1: Define the versioned reconciliation policy and pure contracts

**Files:**
- Create: `glassbox/eval/policies/reconciliation_v1.toml`
- Create: `glassbox/eval/reconciliation.py`
- Create: `tests/eval/test_reconciliation.py`

**Interfaces:**
- Produces `ReconciliationPolicy`, `ReconciliationRule`, `OutcomeInput`, `RejectedOutcome`, `ImportSummary`, and `ReconciliationReport` from `glassbox.eval.reconciliation`.
- Produces `load_policy(path: Path) -> ReconciliationPolicy`, `parse_jsonl(lines: Iterable[str]) -> tuple[OutcomeInput | RejectedOutcome, ...]`, `derive_label(policy, decision, outcome) -> str`, and `calculate_report(policy, source, *, as_of: datetime) -> ReconciliationReport`.
- Consumes only JSON-safe values and typed source values supplied later by the store; it must not open SQLite or import `glassbox.web`, `glassbox.sdk`, or `glassbox.collector`.

- [ ] **Step 1: Write failing policy, pointer, and reporting tests.**

  Build a policy fixture with one `replenishment-triage` / `triage` / `stockout_occurred` rule:

  ```toml
  policy_version = "reconciliation_v1"
  maturity_days = 30

  [[rules]]
  agent_name = "replenishment-triage"
  decision_type = "triage"
  outcome_type = "stockout_occurred"
  recommendation_pointer = "/action"
  positive_recommendation_values = ["expedite", "order"]
  outcome_pointer = "/occurred"
  positive_outcome_value = true
  ```

  Add exact tests:

  ```python
  @pytest.mark.parametrize(
      ("action", "occurred", "label"),
      [("order", True, "tp"), ("order", False, "fp"),
       ("hold", True, "fn"), ("hold", False, "tn")],
  )
  def test_rule_derives_every_confusion_matrix_label(...): ...

  def test_policy_rejects_zero_and_ambiguous_rules(...): ...
  def test_pointer_rejects_missing_or_non_scalar_target(...): ...
  def test_jsonl_parser_keeps_line_number_and_safe_reason(...): ...
  def test_report_excludes_young_decisions_and_uses_null_denominators(...): ...
  def test_report_keeps_policy_hash_cohorts_separate_and_uses_latest_outcome(...): ...
  ```

  Use a fixed `AS_OF = datetime(2026, 10, 5, tzinfo=UTC)`. Construct source samples with a decision 31 days old, a decision 29 days old, labels `tp`/`fp`/`tn`/`fn`, an unlabelled mature decision, and two same-type outcomes differing only in observed time. Assert coverage uses mature decisions, precision is `tp / (tp + fp)`, recall is `tp / (tp + fn)`, and a zero denominator is `None`.

- [ ] **Step 2: Run the focused tests and confirm they fail before implementation.**

  Run: `pytest tests/eval/test_reconciliation.py -q`

  Expected: collection fails because `glassbox.eval.reconciliation` does not exist.

- [ ] **Step 3: Implement strict policy/hash and input contracts.**

  In `glassbox/eval/reconciliation.py`, define immutable dataclasses. Canonicalize the parsed TOML with sorted, compact JSON and compute `policy_hash = hashlib.sha256(...).hexdigest()`. Reject unknown top-level/rule keys, non-positive maturity, empty strings, duplicate matching rules, non-string pointer tokens, non-scalar `positive_outcome_value`, and empty positive-recommendation lists.

  Implement RFC 6901 key traversal for object mappings only: a pointer is `""` for the root or slash-separated tokens, with `~1` decoded to `/` and `~0` decoded to `~`; arrays, absent keys, and container targets raise a typed `ReconciliationError("invalid_pointer")`. Compare scalars by JSON type and value, so `true` never equals `1`.

  Parse each non-blank JSONL line independently. Return a `RejectedOutcome(line_number, source_id_or_none, reason)` for invalid JSON, invalid shape, or invalid UTC timestamp; retain the original line number without exposing raw values in the reason.

- [ ] **Step 4: Implement deterministic report calculation.**

  Define source-only dataclasses containing decision ID, agent name, decision type, recommendation, decided-at instant, compatible outcomes, and whether an effective override exists. `calculate_report` must:

  1. require UTC `as_of`;
  2. select mature decisions with `decided_at <= as_of - timedelta(days=policy.maturity_days)`;
  3. find the one matching policy rule per decision, then choose the newest compatible, same-policy-hash outcome by `(observed_at, outcome_id)` descending;
  4. count mature, labelled, `tp`, `fp`, `tn`, `fn`, precision, recall, and override counts grouped by decision type;
  5. return JSON-ready data whose policy provenance and `as_of` are explicit.

- [ ] **Step 5: Run focused checks.**

  Run:

  ```shell
  pytest tests/eval/test_reconciliation.py -q
  ruff check glassbox/eval/reconciliation.py tests/eval/test_reconciliation.py
  mypy glassbox/eval/reconciliation.py
  ```

  Expected: policy/hash, pointer/rule, maturity, latest-outcome, and all label tests pass.

- [ ] **Step 6: Commit the pure reconciliation layer.**

  ```shell
  git add glassbox/eval/reconciliation.py glassbox/eval/policies/reconciliation_v1.toml tests/eval/test_reconciliation.py
  git commit -m "feat: define reconciliation policy and metrics"
  ```

## Task 2: Migrate and persist replay-safe outcome records

**Files:**
- Create: `glassbox/store/migrations/005_outcome_reconciliation.sql`
- Modify: `glassbox/store/schema.sql`
- Modify: `glassbox/store/database.py`
- Modify: `glassbox/store/repository.py`
- Modify: `tests/store/test_database.py`
- Modify: `tests/store/test_repository.py`
- Modify: `tests/store/test_schema.py`

**Interfaces:**
- Consumes `OutcomeSubmission` and report-source dataclasses owned by `glassbox.store.repository`; eval supplies only already-validated primitives.
- Produces `Repository.record_outcome(submission: OutcomeSubmission) -> OutcomeRecord` and `Repository.reconciliation_source(policy_hash: str) -> tuple[ReconciliationDecisionSource, ...]`.
- Produces a current P4 database schema accepted by `Database.open_read_only`; store never imports `glassbox.eval`.

- [ ] **Step 1: Write failing migration and repository tests.**

  Add tests that create a released P3b database, seed a legacy outcome, call `Database.open`, and assert it upgrades once with legacy `source_id`, policy version, and policy hash all `NULL`. Add a current-schema read-only test proving it does not mutate or recreate any table.

  Add repository tests:

  ```python
  def test_record_outcome_is_append_only_and_idempotent_for_identical_source_id(...): ...
  def test_record_outcome_rejects_conflicting_source_id_without_writes(...): ...
  def test_reconciliation_source_uses_latest_compatible_outcome_and_effective_override(...): ...
  ```

  Seed a decision, three outcomes with two observations for one `(decision_id, outcome_type)`, a different-policy outcome, and an override chain. Assert the source query retains only the latest matching policy outcome and reports whether the decision has an effective override. Include timestamp precision ties and use `outcome_id` as the tie breaker.

- [ ] **Step 2: Run failing store tests.**

  Run:

  ```shell
  pytest tests/store/test_database.py tests/store/test_repository.py tests/store/test_schema.py -q -k outcome
  ```

  Expected: failures because migration 005 and outcome repository methods are absent.

- [ ] **Step 3: Implement migration 005 and schema fingerprints.**

  Rebuild only `outcomes` inside `BEGIN IMMEDIATE` with foreign keys temporarily disabled, following `_apply_judge_calibration_migration`'s rollback/finally pattern. Copy every legacy column into the replacement and populate `source_id`, `reconciliation_policy_version`, and `reconciliation_policy_hash` as `NULL`. The replacement DDL must retain the existing ULID, decision FK, strict UTC observed-at, non-negative horizon, JSON value, and label checks; add:

  ```sql
  source_id TEXT,
  reconciliation_policy_version TEXT,
  reconciliation_policy_hash TEXT,
  CHECK ((source_id IS NULL) = (reconciliation_policy_version IS NULL)),
  CHECK ((source_id IS NULL) = (reconciliation_policy_hash IS NULL)),
  CHECK (reconciliation_policy_hash IS NULL OR length(reconciliation_policy_hash) = 64)
  );
  CREATE UNIQUE INDEX idx_outcomes_source_id
  ON outcomes(source_id) WHERE source_id IS NOT NULL;
  ```

  Update `_TABLES`, `_INDEXES`, current/released schema fingerprint functions, and `_initialize_schema` so fresh, pre-strict, P2, P3a, and P3b databases each apply the right ordered migrations exactly once. Keep `open_read_only()` free of migration work. Update `schema.sql` and `test_schema_sql_matches_the_released_migrations()` to include `005_outcome_reconciliation.sql` exactly.

- [ ] **Step 4: Add typed outcome persistence and source reads.**

  Add these store-owned dataclasses near `FeedbackSubmission`:

  ```python
  @dataclass(frozen=True)
  class OutcomeSubmission:
      outcome_id: str
      source_id: str
      decision_id: str
      outcome_type: str
      observed_at: datetime
      horizon_days: int
      value: Any
      label: Literal["tp", "fp", "tn", "fn"]
      reconciliation_policy_version: str
      reconciliation_policy_hash: str
  ```

  Validate non-empty string fields, a canonical UTC timestamp, non-negative non-bool horizon, exact label, 64-character hash, and JSON-serializable value before SQL. `record_outcome` must acquire the repository lock, serialize `value` canonically, look up `source_id`, and compare only caller-provided fields. A matching row returns unchanged; a conflict raises `ValueError`; otherwise insert and return the typed row.

  `reconciliation_source(policy_hash)` must use bound values and `glassbox_timestamp_key` ordering. It returns decision recommendation/type/agent/decided time, the newest outcome for each `(decision_id, outcome_type)` carrying that policy hash, and whether exactly one current override head exists. The eval layer selects an outcome type through the policy rule; the store must not interpret policy JSON or calculate labels.

- [ ] **Step 5: Run store verification.**

  Run:

  ```shell
  pytest tests/store/test_database.py tests/store/test_repository.py tests/store/test_schema.py -q
  ruff check glassbox/store tests/store
  mypy glassbox/store
  ```

  Expected: legacy outcomes survive the upgrade, imported rows require provenance, source IDs are replay-safe, and strict read-only validation accepts only the P4 schema.

- [ ] **Step 6: Commit storage support.**

  ```shell
  git add glassbox/store tests/store
  git commit -m "feat: persist reconciled outcomes"
  ```

## Task 3: Add JSONL import orchestration and CLI commands

**Files:**
- Modify: `glassbox/eval/reconciliation.py`
- Modify: `glassbox/cli.py`
- Modify: `tests/eval/test_reconciliation.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces `import_outcomes(database_path: Path, input_path: Path, reject_path: Path, policy: ReconciliationPolicy, *, clock: Callable[[], datetime]) -> ImportSummary`.
- Produces `run_reconciliation_report(database_path: Path, policy: ReconciliationPolicy, *, clock: Callable[[], datetime]) -> ReconciliationReport`.
- Adds `glassbox outcomes import --input PATH --rejects PATH [--policy PATH]` and `glassbox outcomes report [--policy PATH]`.

- [ ] **Step 1: Write failing importer and CLI tests.**

  Add tests that seed two persisted decisions and import a JSONL file containing one valid row, one unknown decision, one malformed JSON line, one exact replay, and one source-ID conflict. Assert accepted/replayed/rejected counts, database row counts, reject JSONL records ordered by input line, and no raw source payload in reject reasons.

  Add CLI tests:

  ```python
  def test_outcomes_import_returns_one_and_writes_rejects_for_partial_success(...): ...
  def test_outcomes_import_returns_zero_for_clean_idempotent_replay(...): ...
  def test_outcomes_commands_return_two_for_invalid_policy_or_io(...): ...
  def test_outcomes_report_prints_policy_provenance_and_null_metrics(...): ...
  ```

  Assert a source conflict causes only its line to reject, not rollback a prior valid line; an unreadable `--rejects` destination fails with exit `2` before database import begins.

- [ ] **Step 2: Run the focused tests and confirm the CLI is absent.**

  Run:

  ```shell
  pytest tests/eval/test_reconciliation.py tests/test_cli.py -q -k outcomes
  ```

  Expected: parser rejects `outcomes` as an unknown command and orchestration functions do not exist.

- [ ] **Step 3: Implement importer and atomic reject artifact.**

  In `import_outcomes`, validate that input and reject paths do not resolve to the same file; load/validate the policy before opening either database or input. Parse the input line-by-line, resolve the persisted decision through repository data, choose exactly one rule, derive the label, and call `record_outcome` for each acceptable line. Convert expected invalid row conditions to `RejectedOutcome`; let unexpected database/IO failures abort as operational errors.

  Write reject objects with only:

  ```json
  {"line": 4, "source_id": "erp-stockout-88421", "reason": "unknown_decision"}
  ```

  Write to `reject_path.with_name(f".{reject_path.name}.tmp")`, flush and `os.fsync`, then `Path.replace(reject_path)`. If there are no rejects, write an empty reject file atomically. Never include raw `value`, recommendation, database path, or SQLite exception text in JSON output.

  `run_reconciliation_report` captures `as_of` once, opens a read-only database, obtains `Repository.reconciliation_source(policy.policy_hash)`, calls `calculate_report`, and closes in `finally`; it never writes an outcome or report row.

- [ ] **Step 4: Add lazy CLI dispatch.**

  Add an `outcomes` parser with nested `import` and `report` commands. Import `ReconciliationError`, `default_policy_path`, `import_outcomes`, `load_policy`, and `run_reconciliation_report` only inside `if arguments.command == "outcomes":`. Print compact sorted JSON. Catch policy validation, safe importer errors, `OSError`, SQLite, and migration errors; print exactly `glassbox: unable to run outcomes command` to stderr and return `2`.

  For completed import, return `1 if summary.rejected else 0`. For report, always return `0` after output, even with no mature or labelled decisions.

- [ ] **Step 5: Run focused checks.**

  Run:

  ```shell
  pytest tests/eval/test_reconciliation.py tests/test_cli.py -q
  ruff check glassbox/eval/reconciliation.py glassbox/cli.py tests/eval/test_reconciliation.py tests/test_cli.py
  mypy glassbox/eval/reconciliation.py glassbox/cli.py
  ```

  Expected: partial imports preserve accepted rows and exit `1`; clean replays exit `0`; operational failures exit `2`; report output never changes the database.

- [ ] **Step 6: Commit CLI outcomes workflow.**

  ```shell
  git add glassbox/eval/reconciliation.py glassbox/cli.py tests/eval/test_reconciliation.py tests/test_cli.py
  git commit -m "feat: import and reconcile outcomes"
  ```

## Task 4: Enforce boundaries, document operation, and release-gate P4

**Files:**
- Modify: `tests/test_architecture.py`
- Modify: `README.md`
- Modify: `TODO.md`
- Modify: relevant tests only if release verification exposes a P4 defect

**Interfaces:**
- Ensures `glassbox.eval.reconciliation` is the only P4 eval import the CLI uses, and that it remains unavailable to SDK, collector, and web through existing import-linter contracts.
- Documents only the shipped CLI import/report workflow; P5 dashboard, alerts, scheduling, and CI automation remain open.

- [ ] **Step 1: Write failing boundary tests.**

  Add AST assertions that `glassbox.cli` has no module-level `glassbox.eval.reconciliation` import and that a temporary source file with a top-level reconciliation import fails the helper. Assert the existing `sdk-dependencies`, `collector-dependencies`, and `web-dependencies` contracts each forbid `glassbox.eval` or an equivalent strict superset. Do not add a new import-linter contract.

- [ ] **Step 2: Run architecture tests.**

  Run: `pytest tests/test_architecture.py -q`

  Expected: the production CLI passes because reconciliation is already lazily imported in Task 3. The companion temporary-source case proves the AST helper rejects a prohibited module-level import.

- [ ] **Step 3: Document operator flow and update completion status.**

  Add this README example:

  ```shell
  glassbox outcomes import \
    --input outcomes.jsonl \
    --rejects outcomes.rejects.jsonl
  glassbox outcomes report
  ```

  Explain explicit decision IDs, source-ID replay behavior, required reject artifact, the 30-day maturity window, policy-hash cohort isolation, and exact `0`/`1`/`2` outcomes-command exits. In `TODO.md`, mark only outcome ingestion/deferred-truth reconciliation complete; leave counterfactual providers, Discord alerts, scheduling/CI gates, P5 dashboard generation, and planner usability open.

- [ ] **Step 4: Run the release gate.**

  Run:

  ```shell
  pytest -q
  ruff check .
  mypy glassbox
  lint-imports
  git diff --check
  ```

  Then inspect `git status --short`: do not stage generated SQLite files, WAL/SHM sidecars, outcome input/reject fixtures, `.env`, or `.superpowers/brainstorm/` artifacts.

- [ ] **Step 5: Perform one manual CLI smoke test.**

  Seed a temporary database with one decision whose recommendation matches the checked-in policy, create a one-row JSONL outcome file, and run:

  ```shell
  glassbox --database /tmp/glassbox-outcomes-smoke.sqlite3 outcomes import \
    --input /tmp/glassbox-outcomes.jsonl \
    --rejects /tmp/glassbox-outcomes.rejects.jsonl
  glassbox --database /tmp/glassbox-outcomes-smoke.sqlite3 outcomes report
  ```

  Confirm the import JSON says one accepted/zero rejected, the reject file is empty, the report includes policy hash plus mature/labelled counts, and rerunning the same import yields an idempotent replay with no second outcome row.

- [ ] **Step 6: Commit safeguards and documentation.**

  ```shell
  git add tests/test_architecture.py README.md TODO.md
  git commit -m "docs: document outcome reconciliation"
  ```

## Plan self-review

- **Spec coverage:** Task 1 implements the versioned 30-day policy, strict pointers, four labels, maturity, latest compatible outcome, and null metric rules. Task 2 implements the legacy-safe migration, provenance, idempotency, and store-only SQL. Task 3 implements per-line reject handling, required atomic reject artifact, exact CLI exits, and report orchestration. Task 4 protects lazy imports, documents operation, verifies all gates, and retains deferred scope.
- **No placeholders:** Every task names concrete files, types, command syntax, test cases, expected command behavior, and commit boundaries.
- **Type consistency:** `OutcomeSubmission` is store-owned because the repository persists it; `ReconciliationPolicy`, label derivation, import/report orchestration, and JSONL values are eval-owned. The CLI depends only on eval public names inside its outcomes branch.

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-10-05-glassbox-p4-outcome-reconciliation.md`.

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.
