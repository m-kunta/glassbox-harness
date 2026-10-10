# P3 Counterfactual Prompt Variants Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit, auditable CLI experiment that runs a versioned prompt variant against recorded decisions and optionally enriches its hypothetical recommendation with a mature P4 outcome label.

**Architecture:** `glassbox.eval.counterfactual` owns prompt parsing, snapshot construction, response validation, provider orchestration, and report shaping. `glassbox.store` owns migration 006 and all SQLite queries/inserts; it returns typed, policy-neutral decision/outcome source data. The CLI performs configuration validation, database candidate disclosure, and egress consent before it constructs a provider; counterfactual code is imported only in that CLI branch.

**Tech Stack:** Python 3.11, stdlib `tomllib`/`json`/`hashlib`, existing `python-dotenv` judge extra, existing lazy provider adapters, SQLite, Pydantic event records, pytest, Ruff, mypy, import-linter.

## Global Constraints

- The only shipped execution mode is `glassbox counterfactual run`: an explicit offline batch command. Never call a provider in tracing, collection, serving, or a production agent request.
- Prompt artifacts are UTF-8 files with exact `+++` TOML front matter containing a non-empty `prompt_version` and exactly one `{{decision_snapshot_json}}` placeholder in the body. The full artifact SHA-256 is its prompt identity.
- Provider/model configuration is mandatory and uses only `GLASSBOX_COUNTERFACTUAL_PROVIDER`, `GLASSBOX_COUNTERFACTUAL_MODEL`, provider credential, and optional `GLASSBOX_COUNTERFACTUAL_OLLAMA_URL` from the project-root `.env` plus process environment; process values win and dotenv parsing must not mutate `os.environ`.
- Reuse existing optional provider SDK transports lazily. Every adapter receives explicit credential/base URL and uses temperature `0`; no provider SDK is a base dependency.
- Every remote destination requires `--confirm-egress` after a disclosure of provider, model, destination, prompt version/hash, selected count, and capture-content count. Literal loopback Ollama does not; non-loopback Ollama does.
- The prompt receives a structured redacted snapshot plus optional blob content only when `--blob-dir PATH` is supplied. Never infer a blob directory. Provider input excludes confidence, feedback, and outcomes.
- Snapshot JSON is untrusted data inside explicit delimiters. Provider output must be exactly an object with JSON `recommendation` and non-empty string `rationale`.
- One run captures `as_of` once; selects `decisions.decided_at >= as_of - --since` ordered by parsed UTC instant descending then `decision_id` descending; and applies `--max-cases` only after that fixed order.
- Runs/results are append-only audit records. One completed command atomically persists one run and every successful/failed case result; per-case provider/parse/blob failures become result rows and never stop later cases.
- Outcome enrichment uses only the selected reconciliation-policy hash, the newest compatible outcome of its required type, and decisions mature at `as_of - maturity_days`. It derives the label from the hypothetical recommendation and raw observed outcome; it never copies the original decision label.
- No web UI, scheduling, alerts, automatic rollout, spend policy, aggregate winner claim, or live decision-time shadow execution is in scope.
- `sdk`, `collector`, and `web` cannot import counterfactual eval modules. `cli.py` imports them only in the counterfactual command branch.

---

## File structure

| Path | Responsibility |
| --- | --- |
| `glassbox/eval/counterfactual_config.py` | Scoped dedicated configuration and remote/loopback classification. |
| `glassbox/eval/counterfactual_provider.py` | Thin adapter over the existing lazy provider transport and strict counterfactual response parsing. |
| `glassbox/eval/counterfactual.py` | Prompt artifact parsing, snapshot rendering, blob enrichment, run orchestration, and JSON-ready report. |
| `glassbox/eval/prompts/counterfactual_v1.md` | Checked-in safe default/example prompt artifact. |
| `glassbox/store/migrations/006_counterfactual_prompts.sql` | Counterfactual run/result tables and indexes. |
| `glassbox/store/database.py` / `schema.sql` | P4-to-P3c upgrade path and exact strict-schema fingerprint. |
| `glassbox/store/repository.py` | Candidate reads, immutable run/result transaction, and report reads. |
| `glassbox/cli.py` | Lazy `counterfactual run` and `counterfactual report` commands plus consent preflight. |
| `tests/eval/test_counterfactual_*.py` | Pure prompt/config/provider/orchestration tests. |
| `tests/store/test_database.py`, `tests/store/test_repository.py`, `tests/store/test_schema.py` | Migration, persistence, and deletion-restriction tests. |
| `tests/test_cli.py`, `tests/test_architecture.py`, `README.md`, `TODO.md` | CLI behavior, import boundaries, operator docs, and scope status. |

## Task 1: Create the isolated configuration, transport, and prompt contracts

**Files:**
- Create: `glassbox/eval/counterfactual_config.py`
- Create: `glassbox/eval/counterfactual_provider.py`
- Create: `glassbox/eval/counterfactual.py`
- Create: `glassbox/eval/prompts/counterfactual_v1.md`
- Create: `tests/eval/test_counterfactual_config.py`
- Create: `tests/eval/test_counterfactual_provider.py`
- Create: `tests/eval/test_counterfactual.py`

**Interfaces:**
- Produces `CounterfactualConfig`, `CounterfactualConfigError`, and `load_counterfactual_config(project_root: Path, environ: Mapping[str, str]) -> CounterfactualConfig`.
- Produces `CounterfactualProvider` with `complete(system_prompt: str, user_prompt: str) -> str`; `create_counterfactual_provider(config)` may wrap `create_judge_provider(JudgeConfig(provider=config.provider, model=config.model, credential=config.credential, base_url=config.base_url, is_remote=config.is_remote))` but must not load a provider SDK itself at module import time.
- Produces `PromptArtifact`, `CounterfactualParseResult`, `load_prompt_artifact(path: Path)`, `render_prompt(artifact, snapshot)`, and `parse_counterfactual_response(raw)`.

- [ ] **Step 1: Write failing configuration and artifact tests.**

  Add exact tests for process-over-dotenv precedence, root-only `.env` reading, no environment mutation, missing provider/model/credential rejection, loopback/non-loopback Ollama classification, and provider-only key access. Add prompt tests for each malformed case and a valid fixture:

  ```python
  def test_prompt_requires_exact_front_matter_and_single_snapshot_placeholder(tmp_path: Path):
      path = tmp_path / "variant.md"
      path.write_text('+++\nprompt_version = "v1"\n+++\n{{decision_snapshot_json}}')
      artifact = load_prompt_artifact(path)
      assert artifact.prompt_version == "v1"
      assert artifact.prompt_hash == hashlib.sha256(path.read_bytes()).hexdigest()

  @pytest.mark.parametrize("raw", [
      "{}",
      "[]",
      '{"recommendation": {}}',
      '{"recommendation": {}, "rationale": ""}',
      '{"recommendation": {}, "rationale": "ok", "extra": true}',
  ])
  def test_counterfactual_response_rejects_nonconforming_envelopes(raw: str) -> None:
      with pytest.raises(ValueError):
          parse_counterfactual_response(raw)
  ```

  Include a strict JSON test proving `{"recommendation": {"action": "hold"}, "rationale": "Maintain the current position."}` succeeds and `NaN`, extra top-level keys, and non-object responses fail without raising.

- [ ] **Step 2: Run the focused tests and observe red.**

  Run:

  ```shell
  .venv/bin/pytest tests/eval/test_counterfactual_config.py tests/eval/test_counterfactual_provider.py tests/eval/test_counterfactual.py -q
  ```

  Expected: collection fails because the counterfactual modules do not exist.

- [ ] **Step 3: Implement scoped configuration and transport reuse.**

  Mirror `judge_config.py`'s safe `dotenv_values()` pattern with the counterfactual key names. Build a `CounterfactualConfig(provider, model, credential, base_url, is_remote)` only after all values are valid. Construct an existing `JudgeConfig` from those resolved fields and adapt its `judge()` method to `CounterfactualProvider.complete()`; this reuses the tested explicit-SDK constructor/base-URL/temperature behavior without borrowing judge configuration. Never import `anthropic`, `openai`, or `google.genai` in any new module.

  Implement strict front-matter parsing with `tomllib.loads`, reject unknown metadata keys and empty/non-string versions, and compute the artifact hash from raw UTF-8 bytes. `render_prompt()` must use `canonical_dumps(snapshot)` and replace exactly one token. `parse_counterfactual_response()` must use `json.loads(raw, parse_constant=_reject_non_finite_constant)`, reject extra/missing keys, reject blank rationale, and validate that recommendation is recursively JSON-safe and UTF-8 encodable.

- [ ] **Step 4: Verify green.**

  Run:

  ```shell
  .venv/bin/pytest tests/eval/test_counterfactual_config.py tests/eval/test_counterfactual_provider.py tests/eval/test_counterfactual.py -q
  .venv/bin/ruff check glassbox/eval/counterfactual_config.py glassbox/eval/counterfactual_provider.py glassbox/eval/counterfactual.py tests/eval
  .venv/bin/mypy glassbox/eval/counterfactual_config.py glassbox/eval/counterfactual_provider.py glassbox/eval/counterfactual.py
  ```

  Expected: all focused tests pass; optional provider SDKs remain absent from module imports.

- [ ] **Step 5: Commit the pure contracts.**

  ```shell
  git add glassbox/eval/counterfactual_config.py glassbox/eval/counterfactual_provider.py glassbox/eval/counterfactual.py glassbox/eval/prompts/counterfactual_v1.md tests/eval/test_counterfactual_config.py tests/eval/test_counterfactual_provider.py tests/eval/test_counterfactual.py
  git commit -m "feat: add counterfactual prompt contracts"
  ```

## Task 2: Add counterfactual audit storage and policy-neutral source reads

**Files:**
- Create: `glassbox/store/migrations/006_counterfactual_prompts.sql`
- Modify: `glassbox/store/schema.sql`
- Modify: `glassbox/store/database.py`
- Modify: `glassbox/store/repository.py`
- Modify: `tests/store/test_database.py`
- Modify: `tests/store/test_repository.py`
- Modify: `tests/store/test_schema.py`

**Interfaces:**
- Produces store-owned immutable dataclasses `CounterfactualCandidate`, `CounterfactualOutcomeSource`, `CounterfactualRun`, `CounterfactualResult`, and `CounterfactualRunDetail`.
- Produces `Repository.counterfactual_candidates(decided_since: datetime, policy_hash: str) -> tuple[CounterfactualCandidate, ...]`, `Repository.record_counterfactual_run(run, results) -> None`, and `Repository.counterfactual_run_detail(run_id: str) -> CounterfactualRunDetail | None`.
- Store code never imports `glassbox.eval`; it returns recommendation/rationale/evidence/alternative/capture-reference/raw-outcome facts only.

- [ ] **Step 1: Write failing migration and repository tests.**

  Add a P4 database fixture, upgrade it with `Database.open()`, and assert current read-only open accepts the P3c schema without mutation. Seed decisions, cited evidence, alternatives, trace/span blob references, and same-type P4 outcomes in two policy hashes. Assert:

  ```python
  def test_counterfactual_candidates_are_ordered_and_policy_hash_scoped(repository: Repository) -> None:
      candidates = repository.counterfactual_candidates(since, policy_hash)
      assert [item.decision_id for item in candidates] == [newest_id, older_id]
      assert candidates[0].outcomes[0].value == {"occurred": True}

  ```

  Add `test_counterfactual_run_and_results_are_append_only_and_restrict_deletion`: persist a run and result, then prove updates and deletion of its referenced decision/run are rejected. Add `test_counterfactual_result_disallows_duplicate_decision_within_run`: attempt two rows for one `(run_id, decision_id)` and assert the transaction rolls back.

  Test the selected outcome ordering by `glassbox_timestamp_key(observed_at) DESC, outcome_id DESC`; preserve raw `value`, `horizon_days`, and policy provenance so eval can derive a variant-specific label. Verify `prompt_ref`, `completion_ref`, and trace `input_ref` are returned as references, never read by SQLite.

- [ ] **Step 2: Run failing storage tests.**

  Run:

  ```shell
  .venv/bin/pytest tests/store/test_database.py tests/store/test_repository.py tests/store/test_schema.py -q -k counterfactual
  ```

  Expected: failures because migration 006 and repository methods are absent.

- [ ] **Step 3: Implement migration 006 and strict schema evolution.**

  Add `counterfactual_runs` with ULID primary key; non-empty provider/model/base URL/prompt version/prompt hash/outcome policy version/outcome policy hash; UTC `created_at`/`as_of`; non-negative selected/succeeded/failed counts; and `remote_egress_confirmed` constrained to `0`/`1`. Add `counterfactual_results` with ULID primary key, `run_id` and `decision_id` `ON DELETE RESTRICT` FKs, canonical JSON checks for snapshot/original/hypothetical recommendation, nullable paired hypothetical recommendation/rationale, nullable paired failure code, nullable paired outcome ID/derived label, and `UNIQUE(run_id, decision_id)`. Enforce one of the success/failure result shapes, label membership `tp|fp|tn|fn`, and strict UTC timestamps.

  Extend `_TABLES`, `_INDEXES`, `_initialize_schema()`, released-P4 fingerprint, current fingerprint, and `open_read_only()` validation exactly as migrations 003–005 do. Update `schema.sql` and the byte-for-byte migration mirror test to include 006.

- [ ] **Step 4: Implement typed repository methods.**

  Use only bound SQL. `counterfactual_candidates()` returns all due decisions ordered by `glassbox_timestamp_key(decided_at) DESC, decision_id DESC`, cited evidence grouped by `evidence_id`, persisted rationale/alternatives, trace metadata and blob refs, plus newest exact-policy outcomes by decision/type. It must not decide which policy rule applies or derive labels.

  `record_counterfactual_run()` validates every dataclass before opening `BEGIN IMMEDIATE`, inserts the run and its results in one transaction, and rolls back every write on failure. It never updates a prior run/result. `counterfactual_run_detail()` reconstructs typed report rows ordered by selected-case order and returns `None` for an unknown run.

- [ ] **Step 5: Verify green.**

  Run:

  ```shell
  .venv/bin/pytest tests/store/test_database.py tests/store/test_repository.py tests/store/test_schema.py -q
  .venv/bin/ruff check glassbox/store tests/store
  .venv/bin/mypy glassbox/store
  ```

  Expected: P0–P4 upgrades remain valid, read-only never migrates, and audit records cannot be silently removed.

- [ ] **Step 6: Commit storage support.**

  ```shell
  git add glassbox/store tests/store
  git commit -m "feat: persist counterfactual prompt runs"
  ```

## Task 3: Orchestrate snapshots, independent case failures, and P4 enrichment

**Files:**
- Modify: `glassbox/eval/counterfactual.py`
- Modify: `tests/eval/test_counterfactual.py`
- Modify: `tests/eval/test_reconciliation.py`

**Interfaces:**
- Produces `CounterfactualReport` with `to_dict()`, and `run_counterfactual(database_path: Path, config: CounterfactualConfig, prompt: PromptArtifact, policy: ReconciliationPolicy, decided_since: datetime, max_cases: int | None, blob_dir: Path | None, *, clock: Callable[[], datetime], provider_factory: Callable[[CounterfactualConfig], CounterfactualProvider]) -> CounterfactualReport`.
- Consumes only store-owned candidates and `BlobStore(blob_dir)` for optional capture bytes; no web/SDK/collector code.

- [ ] **Step 1: Write failing orchestration tests.**

  Use a fake provider returning ordered raw JSON responses and a fixed UTC clock. Assert one selected decision always renders a snapshot without blobs, and supplying a blob directory adds only resolvable `input_ref`/`prompt_ref`/`completion_ref` content. Assert the prompt does not contain confidence, feedback, outcome value, or any free-text feedback.

  Add exact behavioral tests:

  Add `test_run_persists_failed_case_and_continues_after_bad_provider_response`, `test_run_derives_variant_label_from_hypothetical_recommendation_and_raw_outcome`, `test_run_leaves_outcome_label_null_when_decision_is_not_mature`, `test_run_uses_fixed_newest_first_selection_before_max_cases`, and `test_blob_read_failure_becomes_a_case_result_not_a_command_failure`. Each test must use a fresh temporary database and inspect the persisted `CounterfactualRunDetail`, not only the in-memory report.

  For enrichment, seed an original `hold` decision and a mature positive raw outcome; fake a hypothetical `order` recommendation and assert the result stores `tp` rather than the original decision's label. Add an unmatched policy-rule case that succeeds but records `outcome_not_applicable`.

- [ ] **Step 2: Run the focused tests and observe red.**

  Run:

  ```shell
  .venv/bin/pytest tests/eval/test_counterfactual.py tests/eval/test_reconciliation.py -q
  ```

  Expected: failures because `run_counterfactual` and its persistence/source wiring do not exist.

- [ ] **Step 3: Implement snapshot and run orchestration.**

  Capture `as_of = clock()` exactly once and reject non-UTC clock values. Open one writer-capable `Database`, retrieve candidates, truncate after fixed ordering, and build a canonical snapshot for each candidate. Resolve blob references only when `blob_dir` is not `None`; decode UTF-8 bytes strictly; represent missing, invalid, unreadable, or non-UTF-8 content as `null` plus a concise per-case capture diagnostic. Do not expose filesystem paths in result/error data.

  Render a system prompt that states the response envelope and a user prompt whose snapshot is delimited by `BEGIN UNTRUSTED DECISION DATA`/`END UNTRUSTED DECISION DATA`. Build the provider only after all local candidate/snapshot preconditions are satisfied. Convert provider exceptions and response parse failures into failed result objects, continue the loop, then atomically record the completed run and all results through the repository.

  For each successful result, use the selected `ReconciliationPolicy` rule and the newest exact-hash outcome from its source candidate. Require maturity relative to the captured `as_of`; call the same policy pointer/label functions used by P4 against the hypothetical recommendation and raw outcome value. Store no label when maturity/rule/outcome applicability fails.

- [ ] **Step 4: Verify green.**

  Run:

  ```shell
  .venv/bin/pytest tests/eval/test_counterfactual.py tests/eval/test_reconciliation.py tests/store/test_repository.py -q
  .venv/bin/ruff check glassbox/eval/counterfactual.py tests/eval
  .venv/bin/mypy glassbox/eval/counterfactual.py
  ```

  Expected: no provider/network dependency is required for tests, and every result is auditable whether its case succeeded or failed.

- [ ] **Step 5: Commit orchestration.**

  ```shell
  git add glassbox/eval/counterfactual.py tests/eval/test_counterfactual.py tests/eval/test_reconciliation.py
  git commit -m "feat: run counterfactual prompt experiments"
  ```

## Task 4: Add CLI preflight, consent, reports, and boundaries

**Files:**
- Modify: `glassbox/cli.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_architecture.py`

**Interfaces:**
- Adds `glassbox counterfactual run --prompt PATH --since DURATION [--max-cases N] [--blob-dir PATH] [--confirm-egress] [--outcome-policy PATH]` and `glassbox counterfactual report --run RUN_ID`.
- Uses existing provider extras; add user-facing `counterfactual`, `counterfactual-claude`, `counterfactual-openai`, and `counterfactual-gemini` aliases with the same dependency lists as their judge counterparts so the feature does not require operators to know an unrelated extra name.

- [ ] **Step 1: Write failing CLI and architecture tests.**

  Add tests using a monkeypatched fake provider/config for:

  Add `test_counterfactual_remote_preflight_requires_confirmation_before_provider_factory`, `test_counterfactual_loopback_ollama_discloses_without_confirmation`, `test_counterfactual_run_prints_compact_json_and_zero_for_partial_case_failures`, and `test_counterfactual_report_returns_two_for_unknown_run_and_never_writes`. Use a fresh temporary database per test and invoke the parser entry point rather than command internals.

  Assert remote disclosure contains provider/model/base URL/prompt version/hash/selected count/capture count but never credentials or snapshot content. Assert malformed prompt/configuration/missing database/missing consent return `2` with one generic safe stderr line and no provider construction. Extend the AST helper fixtures to reject direct, parent-package, relative, module-control-block, and class-body imports of all counterfactual modules, while permitting imports inside the command-handler function branch. Extend `web-dependencies` to forbid `counterfactual`, `counterfactual_config`, and `counterfactual_provider` specifically while preserving the established `web -> eval.drift` allowance.

- [ ] **Step 2: Run focused tests and observe red.**

  Run:

  ```shell
  .venv/bin/pytest tests/test_cli.py tests/test_architecture.py -q -k counterfactual
  ```

  Expected: the parser rejects the command and the new import contract/module checks fail before implementation.

- [ ] **Step 3: Implement lazy CLI branches and exact exits.**

  Add nested `counterfactual run`/`report` argparse commands. Import counterfactual configuration, orchestration, and optional transport only inside `if arguments.command == "counterfactual"`. For `run`, load prompt/policy/config, parse one duration cutoff from a single captured `datetime.now(UTC)`, open/read candidates for disclosure, print it to stderr, and require `--confirm-egress` only when `config.is_remote`. Then call `run_counterfactual()` with the same cutoff and output compact sorted report JSON.

  For `report`, use a read-only database and print compact sorted JSON from `counterfactual_run_detail`; an unknown run and every operational failure emits exactly `glassbox: unable to run counterfactual command` to stderr and exits `2`. A completed `run`, including failed individual cases, exits `0`. Do not catch provider failure broadly in a way that hides failures before a run can be persisted; those are handled inside `run_counterfactual()`.

- [ ] **Step 4: Verify green.**

  Run:

  ```shell
  .venv/bin/pytest tests/test_cli.py tests/test_architecture.py tests/eval/test_counterfactual*.py -q
  .venv/bin/ruff check glassbox/cli.py glassbox/eval tests/test_cli.py tests/test_architecture.py
  .venv/bin/mypy glassbox
  .venv/bin/lint-imports
  ```

  Expected: unrelated commands do not load counterfactual code, and no remote call can occur without explicit consent.

- [ ] **Step 5: Commit command and boundaries.**

  ```shell
  git add glassbox/cli.py pyproject.toml tests/test_cli.py tests/test_architecture.py
  git commit -m "feat: add counterfactual CLI workflow"
  ```

## Task 5: Document, release-gate, and manually smoke-test P3c

**Files:**
- Modify: `README.md`
- Modify: `TODO.md`
- Modify: `tests/test_architecture.py` only if a release-gate defect needs a regression

- [ ] **Step 1: Document the operator workflow.**

  Add README instructions for installing the `counterfactual` provider extra, project-root configuration, a versioned prompt artifact, and these commands:

  ```shell
  glassbox counterfactual run \
    --prompt glassbox/eval/prompts/counterfactual_v1.md \
    --since 7d --blob-dir /path/to/redacted-blobs --confirm-egress
  glassbox counterfactual report --run RUN_ID
  ```

  Explain dedicated credentials/configuration, egress disclosure, why blob location is explicit and optional, immutable run history, P4 enrichment limits, and that a result is not an aggregate winner claim. Mark only the offline counterfactual-provider item complete in `TODO.md`; leave live shadow execution, alerts, scheduling/CI, and dashboard work open.

- [ ] **Step 2: Run the complete release gate.**

  Run:

  ```shell
  .venv/bin/pytest -q
  .venv/bin/ruff check .
  .venv/bin/mypy glassbox
  .venv/bin/lint-imports
  git diff --check
  ```

  Expected: all suites and six architecture contracts pass. Inspect `git status --short`; do not stage databases, WAL/SHM files, blobs, `.env`, provider logs, or `.superpowers/` artifacts.

- [ ] **Step 3: Run a local no-egress smoke test.**

  Seed a temporary current-schema database with one decision/evidence/alternative and a matching mature outcome. Configure loopback Ollama only through a fake/monkeypatched provider adapter; run the command with a checked-in prompt and `--blob-dir` containing one valid redacted capture. Assert the emitted run has one successful result, a variant-specific derived label, no remote-confirmation requirement, and a report that reproduces its stored provenance. Rerun the report through `Database.open_read_only()` and verify the database bytes do not change.

- [ ] **Step 4: Commit docs and release safeguards.**

  ```shell
  git add README.md TODO.md tests/test_architecture.py
  git commit -m "docs: document counterfactual prompt workflow"
  ```

## Plan self-review

- **Spec coverage:** Task 1 implements dedicated scoped configuration, checked-in prompt identity, transport reuse, delimiters, and strict response shape. Task 2 implements the P3c migration, audit records, raw source/capture/outcome reads, and exact schema safety. Task 3 implements fixed selection, optional blob capture, failure isolation, immutable persistence, and P4 variant-label enrichment. Task 4 implements egress consent, CLI/report behavior, extras, lazy imports, and architecture restrictions. Task 5 documents the operator flow and completes release verification.
- **No placeholders:** Each task names files, functions/types, expected command behavior, test cases, and commit boundaries. Deferred work is stated only as explicit out-of-scope policy, not an implementation step.
- **Type consistency:** Store owns `CounterfactualCandidate`, `CounterfactualRun`, and `CounterfactualResult`; eval owns prompt/config/provider/report types and only consumes store values. CLI consumes only eval public names inside its command branch. The P4 `ReconciliationPolicy` remains the sole label-derivation authority.

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-10-09-glassbox-p3-counterfactual-prompts.md`.

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.
