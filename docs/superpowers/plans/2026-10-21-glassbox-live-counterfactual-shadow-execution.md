# Live Counterfactual Shadow Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit, bounded, fail-open background path that evaluates a persisted production decision with an approved counterfactual prompt.

**Architecture:** `glassbox.eval.shadow` owns opt-in admission, bounded worker lifecycle, source-decision polling, diagnostics, and the production-safe API an agent calls directly. The existing counterfactual module remains the single owner of snapshot construction, prompt rendering, parsing, provider adaptation, and immutable result construction; `store` owns the schema migration and typed source/audit reads. The SDK, collector, and web packages do not import shadowing code.

**Tech Stack:** Python 3.11+, SQLite/WAL, stdlib `threading` and `queue`, existing optional provider SDK adapters, pytest, mypy, Ruff, import-linter.

## Global Constraints

- Shadow execution is disabled unless process environment value `GLASSBOX_SHADOW_ENABLED` is exactly `"1"`.
- A remote provider additionally requires `GLASSBOX_SHADOW_ALLOW_EGRESS="1"`; literal-loopback Ollama does not.
- The agent calls the eval-side integration explicitly after normal decision completion. The SDK, collector, and web packages remain independent of it.
- The caller never waits for SQLite, source-decision persistence, provider construction, a provider call, parsing, or audit persistence; every shadow failure is observational only.
- The process permits one active job, one queued job, and at most ten accepted jobs over its lifetime. Rejected admissions are counted locally and do not create database rows.
- Before egress, a worker polls for at most five seconds for the source decision to exist. It then has a ten-second provider request deadline. An unavailable decision never reaches a provider.
- Normal immutable `counterfactual_runs`/`counterfactual_results` history is used only after a source decision exists. No orphan-attempt table is added.
- Add `execution_mode` (`offline` or `live_shadow`) to `counterfactual_runs`; historic rows migrate as `offline`. One shadow case produces one `live_shadow` run and one result.
- Live-shadow results use `outcome_not_applicable`; no decision-time reconciliation or outcome enrichment occurs.
- Reuse the existing fact-only prompt projection. Never send original recommendation, rationale, confidence, feedback, or outcomes to a provider.
- Provider SDK imports stay lazy. Every new eval sibling is explicitly listed in the web forbidden-import contract.

---

## File structure

| File | Responsibility |
| --- | --- |
| `glassbox/store/migrations/009_counterfactual_execution_mode.sql` | Append the run execution-mode column without rewriting immutable history. |
| `glassbox/store/database.py` | Recognize and migrate the exact 008 schema to the 009 schema. |
| `glassbox/store/repository.py` | Type, validate, persist, and load execution mode; expose one source-decision candidate read. |
| `glassbox/eval/counterfactual_provider.py` | Create lazy counterfactual transports with a finite request timeout. |
| `glassbox/eval/counterfactual.py` | Share single-candidate execution between offline batches and a live shadow, with explicit mode and outcome-enrichment control. |
| `glassbox/eval/shadow.py` | Own explicit admission, worker bounds, egress gate, source polling, local counters, and fail-open execution. |
| `tests/store/test_schema.py`, `tests/store/test_repository.py` | Exercise migration, strict mode validation, and single-decision candidate lookup. |
| `tests/eval/test_counterfactual.py`, `tests/eval/test_counterfactual_provider.py`, `tests/eval/test_shadow.py` | Cover runner mode/outcome behavior, request deadline, and live worker isolation. |
| `tests/test_architecture.py`, `pyproject.toml` | Enforce the shadow import boundary and lazy optional-provider behavior. |
| `README.md`, `TODO.md` | Document deliberate agent integration and mark the completed roadmap item only after implementation verification. |

## Task 1: Persist execution mode and expose one exact candidate

**Files:**
- Create: `glassbox/store/migrations/009_counterfactual_execution_mode.sql`
- Modify: `glassbox/store/database.py`
- Modify: `glassbox/store/repository.py`
- Modify: `tests/store/test_schema.py`
- Modify: `tests/store/test_repository.py`

**Interfaces:**
- Produces `CounterfactualRun.execution_mode: Literal["offline", "live_shadow"] = "offline"`.
- Produces `Repository.counterfactual_candidate(decision_id: str, policy_hash: str) -> CounterfactualCandidate | None`.
- Existing `counterfactual_candidates(decided_since, policy_hash)` delegates to the same private row-to-candidate assembly so the two reads cannot diverge.

- [ ] **Step 1: Write migration and repository tests first**

```python
def test_counterfactual_execution_mode_migrates_legacy_rows_as_offline(tmp_path: Path) -> None:
    # Build the exact 008 schema fixture, insert a valid old run, then open it.
    database = Database.open(legacy_008_path)
    try:
        assert database.connection.execute(
            "SELECT execution_mode FROM counterfactual_runs"
        ).fetchone()[0] == "offline"
    finally:
        database.close()


def test_counterfactual_candidate_reads_one_exact_decision(repository: Repository) -> None:
    candidate = repository.counterfactual_candidate(DECISION_ID, "a" * 64)
    assert candidate is not None
    assert candidate.decision.event.decision_id == DECISION_ID
    assert repository.counterfactual_candidate(MISSING_DECISION_ID, "a" * 64) is None
```

Add direct SQL-invalid-mode coverage (`"batch"` is rejected) and assert a run written with `execution_mode="live_shadow"` round-trips through `counterfactual_run_detail()`.

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `./.venv/bin/pytest tests/store/test_schema.py tests/store/test_repository.py -q -k 'counterfactual and (execution_mode or candidate)'`

Expected: FAIL because migration 009, `execution_mode`, and `counterfactual_candidate()` do not exist.

- [ ] **Step 3: Add the additive migration and exact-schema transition**

Create migration 009 exactly as:

```sql
ALTER TABLE counterfactual_runs ADD COLUMN execution_mode TEXT NOT NULL
    DEFAULT 'offline'
    CHECK (execution_mode IN ('offline', 'live_shadow'));
```

In `database.py`, add an exact `_released_008_schema_objects()` fingerprint for the current pre-009 shape, then make `_current_schema_objects()` apply 007, 008, and 009 in an in-memory connection. Add the one 008-to-current dispatcher branch and include the 008 fingerprint in `_migration_needed()` release ordering. Keep strict read-only open accepting only the final schema.

- [ ] **Step 4: Add typed storage behavior without duplicating candidate assembly**

Extend the frozen `CounterfactualRun` dataclass and the run INSERT column/value lists with `execution_mode`. Validate it before `BEGIN IMMEDIATE`:

```python
if run.execution_mode not in {"offline", "live_shadow"}:
    raise ValueError("counterfactual execution mode is invalid")
```

Extract the body that builds `CounterfactualCandidate` from one decision row into a private repository helper. `counterfactual_candidate()` first fetches that one row by bound `decision_id`, returns `None` when absent, and otherwise calls the helper. Keep the existing policy-hash-scoped outcome query and citation-group behavior unchanged.

- [ ] **Step 5: Run focused verification**

Run: `./.venv/bin/pytest tests/store/test_schema.py tests/store/test_repository.py -q -k counterfactual`

Expected: PASS.

- [ ] **Step 6: Commit the independently reviewable persistence unit**

```bash
git add glassbox/store/migrations/009_counterfactual_execution_mode.sql glassbox/store/database.py glassbox/store/repository.py tests/store/test_schema.py tests/store/test_repository.py
git commit -m "feat: distinguish live counterfactual runs"
```

## Task 2: Make one-case evaluation reusable and deadline-aware

**Files:**
- Modify: `glassbox/eval/counterfactual.py`
- Modify: `glassbox/eval/counterfactual_provider.py`
- Modify: `glassbox/eval/judge_provider.py`
- Modify: `tests/eval/test_counterfactual.py`
- Modify: `tests/eval/test_counterfactual_provider.py`

**Interfaces:**
- Produces `run_counterfactual(..., execution_mode: Literal["offline", "live_shadow"] = "offline", enrich_outcomes: bool = True, request_timeout_seconds: float | None = None) -> CounterfactualReport`.
- `CounterfactualProvider.complete(system_prompt: str, user_prompt: str, *, timeout_seconds: float | None = None) -> str` accepts a finite per-request deadline and raises `CounterfactualProviderTimeout` only for that deadline.
- A live call passes exactly one `preselected_candidates` item, `execution_mode="live_shadow"`, and `enrich_outcomes=False`.

- [ ] **Step 1: Write failing runner tests**

```python
def test_live_shadow_run_persists_one_case_without_outcome_enrichment(tmp_path: Path) -> None:
    report, detail = _execute(
        tmp_path / "events.sqlite3", FakeProvider([_answer()]),
        execution_mode="live_shadow", enrich_outcomes=False,
    )
    assert report.run.execution_mode == "live_shadow"
    assert detail.results[0].outcome_status == "outcome_not_applicable"
    assert detail.results[0].outcome_id is None


def test_counterfactual_provider_forwards_request_timeout() -> None:
    transport = RecordingJudgeTransport()
    create_counterfactual_provider(CONFIG, transport_factory=lambda _: transport).complete(
        "system", "user", timeout_seconds=10.0
    )
    assert transport.timeout_seconds == 10.0
```

Also add an assertion that a live result's provider prompt omits original recommendation, rationale, confidence, feedback, and outcomes just as the offline regression already does.

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `./.venv/bin/pytest tests/eval/test_counterfactual.py tests/eval/test_counterfactual_provider.py -q -k 'live_shadow or timeout'`

Expected: FAIL because the mode/enrichment/timeout interfaces do not exist.

- [ ] **Step 3: Thread an explicit finite timeout through lazy transports**

Extend the judge transport protocol and implementations with an optional keyword-only timeout. Pass it directly to Anthropic/OpenAI request methods, configure Gemini request timeout through its request options, and pass it to `urllib.request.urlopen()` for Ollama. Preserve each adapter's existing fixed base URL, explicit credential constructor argument, and temperature zero behavior. Do not add a provider package or import an optional SDK at module load.

Adapt the counterfactual transport similarly, preserving compatibility for offline calls whose timeout is `None`. Normalize provider-specific timeout exceptions to `CounterfactualProviderTimeout`, but leave other provider exceptions distinct. Reject non-positive/non-finite supplied timeout values before constructing a provider request. In `run_counterfactual()`, catch that exception before the generic provider-exception branch and persist `failure_code="provider_timeout"`; other errors remain `"provider_failure"`.

- [ ] **Step 4: Refactor the counterfactual runner minimally**

Keep one source of truth for `_prepare_case`, strict parsing, and immutable result construction. Add explicit mode/enrichment parameters, validate:

```python
if execution_mode not in {"offline", "live_shadow"}:
    raise ValueError("counterfactual execution mode is invalid")
if execution_mode == "live_shadow" and enrich_outcomes:
    raise ValueError("live shadow cannot enrich outcomes")
```

Pass `request_timeout_seconds` to `provider.complete()`. When enrichment is false, set `outcome_id = outcome_label = None` and `outcome_status = "outcome_not_applicable"`; do not query or derive outcome data. Pass mode to `CounterfactualRun`. Offline defaults must preserve all present CLI behavior.

- [ ] **Step 5: Run focused verification**

Run: `./.venv/bin/pytest tests/eval/test_counterfactual.py tests/eval/test_counterfactual_provider.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the shared execution unit**

```bash
git add glassbox/eval/counterfactual.py glassbox/eval/counterfactual_provider.py glassbox/eval/judge_provider.py tests/eval/test_counterfactual.py tests/eval/test_counterfactual_provider.py
git commit -m "feat: support bounded live counterfactual cases"
```

## Task 3: Add opt-in bounded shadow admission and worker execution

**Files:**
- Create: `glassbox/eval/shadow.py`
- Create: `tests/eval/test_shadow.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_architecture.py`

**Interfaces:**
- Produces `ShadowArtifact(prompt: PromptArtifact, outcome_policy: ReconciliationPolicy)` and `load_shadow_artifact(prompt_path: Path, outcome_policy_path: Path) -> ShadowArtifact`.
- Produces `LiveShadowExecutor(database_path: Path, project_root: Path, blob_dir: Path | None, *, environ: Mapping[str, str], clock: Callable[[], datetime], monotonic_clock: Callable[[], float], sleep: Callable[[float], None], provider_factory: Callable[[CounterfactualConfig], CounterfactualProvider])`.
- Produces `executor.submit(decision_id: str, artifact: ShadowArtifact) -> None`, `executor.diagnostics -> Mapping[str, int]`, and `executor.shutdown(timeout: float | None = None) -> None`.

- [ ] **Step 1: Write isolated admission/worker tests before the module**

```python
def test_disabled_shadow_returns_without_loading_config_or_calling_provider() -> None:
    executor = LiveShadowExecutor(..., environ={})
    executor.submit(DECISION_ID, artifact)
    assert factories == []
    assert executor.diagnostics["disabled"] == 1


def test_missing_source_decision_never_egresses_and_is_counted(tmp_path: Path) -> None:
    executor = enabled_executor(tmp_path / "events.sqlite3", provider=fake, sleep=advance_clock)
    executor.submit(MISSING_DECISION_ID, artifact)
    wait_for_worker(executor)
    assert fake.calls == []
    assert executor.diagnostics["source_decision_unavailable"] == 1


def test_persisted_source_writes_one_live_shadow_run(tmp_path: Path) -> None:
    _seed(tmp_path / "events.sqlite3")
    executor = enabled_executor(tmp_path / "events.sqlite3", provider=FakeProvider([_answer()]))
    executor.submit(DECISION_ID, artifact)
    wait_for_worker(executor)
    assert stored_detail.run.execution_mode == "live_shadow"
    assert len(stored_detail.results) == 1
```

Include tests for remote rejection without `GLASSBOX_SHADOW_ALLOW_EGRESS=1`, loopback admission without it, full queue, lifetime cap, provider timeout recorded as `case_failed`, parser failure, database write failure, and production-call nonblocking behavior. Use injected clocks/sleep/provider factories and a wait helper with a short test-only deadline; do not use real network calls or fixed sleeps.

- [ ] **Step 2: Run the new tests and confirm they fail**

Run: `./.venv/bin/pytest tests/eval/test_shadow.py -q`

Expected: FAIL because `glassbox.eval.shadow` does not exist.

- [ ] **Step 3: Implement fail-open admission and the worker**

Use `queue.Queue(maxsize=1)` and a daemon `Thread`. Resolve configuration and the remote-every-call egress gate during admission, catching every exception. `submit()` must use `put_nowait()` and return `None` in all cases.

Track counters under a lock with these stable keys: `disabled`, `configuration_unavailable`, `egress_not_authorized`, `queue_full`, `lifetime_cap`, `source_decision_unavailable`, `provider_timeout`, `provider_failure`, `parse_failure`, `storage_failure`, and `completed`. `load_shadow_artifact()` validates an artifact before the application begins submitting jobs; its invalid input raises to setup code rather than adding a production-path failure mode.

For each queued job, poll using a fresh `Database.open_read_only()`/`Repository.counterfactual_candidate()` until five injected monotonic seconds expire. Close every read-only connection in `finally`. If no candidate appears, increment `source_decision_unavailable` and return before provider construction.

When a candidate appears, call `run_counterfactual()` with a one-item preselected tuple, the candidate's `decided_at` as `decided_since`, `execution_mode="live_shadow"`, `enrich_outcomes=False`, and `request_timeout_seconds=10.0`. Inspect the returned one-case failure code to increment `provider_timeout`, `provider_failure`, or `parse_failure`; increment `completed` only for a successful case. Catch all operational exceptions in the worker and classify them as `storage_failure` without re-raising. A timed-out provider call must be rejected by the provider-level deadline from Task 2; do not create a second active provider call while the first has not returned.

- [ ] **Step 4: Enforce structural boundaries**

Add `glassbox.eval.shadow` to `web-dependencies.forbidden_modules` and update `tests/test_architecture.py`'s exact expected contract and counterfactual forbidden-module assertions. Add an AST regression proving that `sdk`, `collector`, and `web` import neither `glassbox.eval.shadow` nor its direct helpers. The agent-facing import remains external application code, not `glassbox.sdk`.

- [ ] **Step 5: Run focused verification**

Run:

```bash
./.venv/bin/pytest tests/eval/test_shadow.py tests/eval/test_counterfactual.py tests/test_architecture.py -q
./.venv/bin/ruff check glassbox/eval/shadow.py tests/eval/test_shadow.py
./.venv/bin/mypy glassbox/eval/shadow.py
./.venv/bin/lint-imports
```

Expected: all commands PASS.

- [ ] **Step 6: Commit the bounded worker unit**

```bash
git add glassbox/eval/shadow.py tests/eval/test_shadow.py pyproject.toml tests/test_architecture.py
git commit -m "feat: add opt-in live counterfactual shadowing"
```

## Task 4: Document explicit agent integration and finish the roadmap item

**Files:**
- Modify: `README.md`
- Modify: `TODO.md`
- Modify: `tests/test_architecture.py`

**Interfaces:**
- Documents an external agent constructing one long-lived `LiveShadowExecutor`, loading an approved `ShadowArtifact`, and calling `submit(decision_id, artifact)` only after `DecisionScope.complete()`.

- [ ] **Step 1: Write a documentation-contract test**

```python
def test_readme_documents_live_shadow_as_explicit_and_fail_open() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text()
    assert "GLASSBOX_SHADOW_ENABLED=1" in readme
    assert "GLASSBOX_SHADOW_ALLOW_EGRESS=1" in readme
    assert "after `DecisionScope.complete()`" in readme
    assert "does not change the production decision" in readme
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `./.venv/bin/pytest tests/test_architecture.py -q -k live_shadow`

Expected: FAIL because the README has no live-shadow guide.

- [ ] **Step 3: Add a concise safe-use guide and roadmap state**

Document installation through the existing counterfactual extras, project-root counterfactual provider configuration, both required environment flags for remote egress, and a code sample with an application-owned executor and artifact. State all hard boundaries: explicit per-decision submission after completion, one active/one queued/ten lifetime jobs, five-second collector wait, ten-second request deadline, local diagnostics, immutable `live_shadow` audit history, fact-only provider input, and no production behavior change.

Mark only the live shadow TODO item complete. Leave dashboard generation, Discord delivery, scheduling/CI gating, and the human P2.4 usability session unchanged.

- [ ] **Step 4: Run complete verification**

Run:

```bash
./.venv/bin/pytest -q
./.venv/bin/ruff check .
./.venv/bin/mypy glassbox
./.venv/bin/lint-imports
git diff --check
```

Expected: all checks PASS (with only pre-existing documented warnings, if any).

- [ ] **Step 5: Commit documentation and completion state**

```bash
git add README.md TODO.md tests/test_architecture.py
git commit -m "docs: document live counterfactual shadowing"
```

## Plan self-review

- **Spec coverage:** Task 1 implements immutable execution-mode provenance and source-FK-safe reads. Task 2 shares the existing fact-only execution path while adding an enforceable request deadline and disabling live outcome enrichment. Task 3 implements explicit opt-in/egress gates, five-second source polling, one-active/one-queued/ten-total limits, local diagnostics, and import isolation. Task 4 documents safe external integration and preserves unrelated roadmap work.
- **Placeholder scan:** No task relies on an unspecified migration, API, counter name, timeout, queue limit, provider call shape, or verification command.
- **Type consistency:** `CounterfactualRun.execution_mode`, `Repository.counterfactual_candidate()`, `ShadowArtifact`, and `LiveShadowExecutor.submit()` are defined before later tasks consume them. Both live runner and persistence use the exact literals `offline` and `live_shadow`.

Plan complete and saved to `docs/superpowers/plans/2026-10-21-glassbox-live-counterfactual-shadow-execution.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task and review between tasks.
2. **Inline Execution** — execute tasks in this session with checkpoints.
