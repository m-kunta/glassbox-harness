# Glassbox P1 Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, CI-compatible evaluation engine and exercise it through the independently versioned replenishment agent using persisted Glassbox decision telemetry.

**Architecture:** `glassbox.eval` provides generic typed contracts, deterministic checks, metrics, a runner, and the `glassbox eval` CLI. The replenishment repository owns its adapter, manifest, and golden data, runs in an explicit evaluation environment, and returns a generic result assembled from the decision/evidence data it persisted.

**Tech Stack:** Python 3.11+, Pydantic 2, PyYAML, jsonschema, SQLite, pytest, Ruff, mypy, import-linter.

## Global Constraints

- Preserve all P0 disabled and fail-open behavior. The replenishment agent must still import and run without Glassbox installed.
- `glassbox.eval` may import only `glassbox.events` and `glassbox.store`; it must not import `collector`, `sdk`, `explain`, or `web`. `store` must not import `eval`.
- The agent repository owns `integrations/replenishment_triage.py`, `goldens/replenishment_triage/`, and its real-agent integration test. Glassbox never adds a sibling checkout to `sys.path`.
- Every golden case represents one exception. Every category, including do-nothing, must pass recommendation-schema, citation-resolution, at-least-three-evidence, and at-least-one-alternative checks.
- Urgency order is `LOW < MEDIUM < HIGH < CRITICAL`; use linear weighted kappa. Keep the source-specification gate at `>= 0.6` until the completed 40-case suite records its observed value.
- Scripted provider execution is the only blocking P1 mode. A live-provider smoke mode is deferred and must reuse the same adapter and cases.
- The evaluation runner records target exceptions as failed case results and continues the suite.

---

### Task 1: Generic evaluation contracts and architectural boundary

**Files:**
- Modify: `pyproject.toml`
- Modify: `tests/test_architecture.py`
- Create: `glassbox/eval/__init__.py`
- Create: `glassbox/eval/models.py`
- Create: `glassbox/eval/target.py`
- Create: `tests/eval/test_models.py`
- Create: `tests/eval/test_target.py`

**Interfaces:**
- Produces immutable Pydantic `GoldenCase`, `DecisionResult`, `EvidenceRecord`, `SuiteDefinition`, `CaseEvaluation`, and `SuiteEvaluation` models.
- Produces `EvaluationTarget(Protocol)` and `load_target(import_path: str) -> EvaluationTarget`.

- [ ] **Step 1: Write failing contract and loader tests.** Cover a valid one-exception case, rejected empty `case_id`, ordered expected urgency, a result containing one structured decision plus evidence, valid `module:attribute` loading, and rejection of a missing attribute or non-callable target.

```python
def test_load_target_returns_callable() -> None:
    target = load_target("tests.eval.fakes:run_case")
    assert target(GoldenCase(case_id="case-001", input={}, expected_labels={})) == FAKE_RESULT
```

- [ ] **Step 2: Run `pytest tests/eval/test_models.py tests/eval/test_target.py -v`; expect import failures because `glassbox.eval` does not exist.**

- [ ] **Step 3: Add `PyYAML>=6.0` to Glassbox runtime dependencies.** Create the models with JSON-compatible `input`, `expected_labels`, and `metadata`; model decision data as mappings, evidence as `evidence_id` plus field mappings, and error as an optional `{type, message}` record. Define the protocol as:

```python
class EvaluationTarget(Protocol):
    def __call__(self, case: GoldenCase) -> DecisionResult: ...
```

Implement `load_target` with `importlib.import_module`, exactly one colon, a callable check, and a descriptive `ValueError`; do not import any integration module from `glassbox.eval`.

- [ ] **Step 4: Add the `eval-dependencies` import-linter contract and architecture assertions.** Set `source_modules = ["glassbox.eval"]` and forbid `glassbox.collector`, `glassbox.sdk`, `glassbox.explain`, and `glassbox.web`; retain the existing `store-dependencies` reverse-edge guard. Update the real import-linter regression count from four to five.

- [ ] **Step 5: Run `pytest tests/eval/test_models.py tests/eval/test_target.py tests/test_architecture.py -v`, `mypy glassbox`, and `lint-imports`; expect success. Commit:**

```bash
git add pyproject.toml glassbox/eval tests/eval tests/test_architecture.py
git commit -m "feat: add evaluation contracts"
```

### Task 2: Deterministic assertions and SLA-aware metrics

**Files:**
- Create: `glassbox/eval/assertions.py`
- Create: `glassbox/eval/metrics.py`
- Create: `tests/eval/test_assertions.py`
- Create: `tests/eval/test_metrics.py`

**Interfaces:**
- Consumes `GoldenCase` and `DecisionResult` from Task 1.
- Produces `evaluate_deterministic(result) -> tuple[AssertionResult, ...]`, `linear_weighted_kappa(expected, predicted) -> float`, `urgency_confusion_matrix(expected, predicted) -> dict[str, dict[str, int]]`, and operational metric helpers.

- [ ] **Step 1: Write failing tests for all four checks.** Use a valid result with a JSON-schema-valid recommendation, three evidence records, citations that resolve to those IDs, and one alternative. Independently mutate it to prove failures for an invalid recommendation, foreign/missing citation, two evidence records, and zero alternatives. Include a valid no-action result and assert it receives the same four checks.

- [ ] **Step 2: Write failing metric tests.** Assert perfect agreement is `1.0`, an adjacent `HIGH`/`MEDIUM` disagreement has more weight than no disagreement, a `CRITICAL`/`LOW` disagreement has three times the linear distance, and one-class inputs return `1.0` only when identical (otherwise `0.0`). Assert all four urgency rows and columns appear in the confusion matrix.

- [ ] **Step 3: Add `jsonschema>=4.0` to Glassbox runtime dependencies, then run the targeted tests; expect missing-module failures.**

- [ ] **Step 4: Implement the checks and metrics.** Validate recommendation data with `jsonschema` against the replenishment recommendation JSON schema supplied by the agent manifest; never accept a schema supplied by a result. Resolve citations against evidence IDs in the same `DecisionResult`. Use ordered indices `{LOW: 0, MEDIUM: 1, HIGH: 2, CRITICAL: 3}` and linear disagreement weights `abs(i - j) / 3` in Cohen's weighted-kappa expected/observed disagreement calculation. Compute p50/p95 latency, total/mean cost, total tokens, tokens per decision, and error rate without third-party numeric libraries.

- [ ] **Step 5: Run `pytest tests/eval/test_assertions.py tests/eval/test_metrics.py -v`; expect success. Commit:**

```bash
git add glassbox/eval/assertions.py glassbox/eval/metrics.py tests/eval
git commit -m "feat: add deterministic evaluation checks"
```

### Task 3: Suite loading, isolated runner, gates, and CLI

**Files:**
- Create: `glassbox/eval/runner.py`
- Modify: `glassbox/cli.py`
- Modify: `glassbox/eval/__init__.py`
- Create: `tests/eval/test_runner.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes the Task 1 target and YAML suite manifest and Task 2 assertion/metric results.
- Produces `run_suite(manifest_path: Path) -> SuiteEvaluation` and `glassbox eval --suite PATH`.

- [ ] **Step 1: Write failing runner tests.** Supply a temporary manifest with a fake target and two YAML case files. Assert each case gets its own temporary SQLite database/collector and trace, a target exception becomes one failed result while the next case runs, and the reported assertion breakdown, linear kappa, operational metrics, and gates are deterministic.

- [ ] **Step 2: Write failing CLI tests.** Assert `glassbox eval --suite path` prints canonical JSON containing `cases`, `assertions`, `metrics`, and `gates`; exits `0` for all passing gates; exits `1` for a gate failure; and exits `2` for unreadable or invalid suite configuration.

- [ ] **Step 3: Run `pytest tests/eval/test_runner.py tests/test_cli.py -v`; expect failures.**

- [ ] **Step 4: Implement YAML loading and runner behavior.** Resolve case paths relative to the manifest, reject duplicated `case_id`s, load the target only after validating the manifest, initialize/flush/shutdown a separate temporary Glassbox collector for each case, and read its persisted trace tree through `Repository`. Apply the four assertions to every returned result, aggregate metrics, then evaluate explicit gate expressions for deterministic pass rate, urgency agreement, and cost per decision. Keep command output machine-readable and sorted.

- [ ] **Step 5: Run targeted tests plus `ruff check .`, `mypy glassbox`, and `lint-imports`; expect success. Commit:**

```bash
git add glassbox/eval glassbox/cli.py tests/eval tests/test_cli.py
git commit -m "feat: add deterministic suite runner"
```

### Task 4: Preserve optional tracing while emitting real decisions

**Repository:** `/Users/MKunta/AGENTS/CODE/AI-driven-replenishment-exception-triage-agent`

**Files:**
- Modify: `src/agent/triage_agent.py`
- Modify: `tests/test_triage_agent.py`

**Interfaces:**
- Consumes Glassbox `trace`, `decision_context`, and `evidence` when installed.
- Produces one persisted decision per `TriageResult`, with evidence IDs cited by its rationale and at least one considered alternative.

- [ ] **Step 1: Write failing tests for the two import modes.** With Glassbox installed, run one result through a recording collector and assert a `DecisionEvent` plus at least three `EvidenceEvent`s share a decision ID and citations resolve. With `glassbox` forcibly unavailable, import `src.agent.triage_agent`, execute `TriageAgent.run`, and assert its ordinary `TriageRunResult` is unchanged.

- [ ] **Step 2: Run the focused agent tests; expect the new decision-event assertion to fail.**

- [ ] **Step 3: Extend the one existing optional import block.** Import `decision_context`, `evidence`, and `trace` together; in `except ImportError`, retain no-op `trace`, add a no-op context manager for `decision_context`, and add a no-op `evidence` callable. Do not require Glassbox in `requirements.txt`.

- [ ] **Step 4: Emit structured telemetry after the final pattern-analysis mutations.** For each `TriageResult`, open `decision_context` with entity `replenishment_exception/<exception_id>`, recommendation `{urgency: priority, action: recommended_action}`, rationale `planner_brief`, citations for the emitted evidence IDs, and explicit alternatives derived from the action/priority decision. Emit stable evidence IDs for the exception's inventory position, demand/supply context, and business-impact/risk context, using the real input record as source data. Keep the mapping in this one boundary and preserve agent return values.

- [ ] **Step 5: Run the focused agent tests and its no-Glassbox fallback test; expect success. Commit in the agent repository:**

```bash
git add src/agent/triage_agent.py tests/test_triage_agent.py
git commit -m "feat: emit Glassbox triage decisions"
```

### Task 5: Agent-owned adapter, scripted provider, and executable seed suite

**Repository:** `/Users/MKunta/AGENTS/CODE/AI-driven-replenishment-exception-triage-agent`

**Files:**
- Modify: `requirements.txt`
- Create: `integrations/__init__.py`
- Create: `integrations/replenishment_triage.py`
- Create: `goldens/replenishment_triage/manifest.yaml`
- Create: `goldens/replenishment_triage/cases/critical_oos.yaml`
- Create: `goldens/replenishment_triage/cases/do_nothing.yaml`
- Create: `schemas/triage_rec.json`
- Create: `tests/test_replenishment_evaluation.py`

**Interfaces:**
- Consumes `glassbox.eval.GoldenCase` and returns `glassbox.eval.DecisionResult` from `run_case`.
- Produces an `evaluation` requirements extra/file that installs Glassbox editable plus the agent's normal requirements, without changing production requirements.

- [ ] **Step 1: Write failing integration tests.** Install the evaluation requirements in the agent virtualenv. Execute `integrations.replenishment_triage:run_case` for each seed case with a scripted `LLMProvider.complete`; assert the real `TriageAgent.run` executes, its persisted trace contributes the returned decision/evidence/citations, no network call occurs, and both a CRITICAL action case and a LOW do-nothing case pass all checks.

- [ ] **Step 2: Run `pytest tests/test_replenishment_evaluation.py -v`; expect import/target failures.**

- [ ] **Step 3: Add evaluation-only dependency installation.** Keep base `requirements.txt` unchanged and create `requirements-eval.txt` containing `-r requirements.txt` and the explicit editable Glassbox checkout used by this workspace. Document that a published/package-index release replaces the local editable line in CI.

- [ ] **Step 4: Implement the adapter and seed files.** The scripted provider returns JSON matching the agent's existing batch parser. Build `EnrichedExceptionSchema` from `GoldenCase.input`, substitute the scripted provider into the real agent's batch processor and pattern analyzer, run the real orchestrator, flush the case collector, and read the sole persisted decision with `Repository.trace_tree`. Return no invented values. Make the manifest target `integrations.replenishment_triage:run_case`, use the agent-local schema and cases, gate deterministic pass rate at `1.0`, urgency agreement at `0.6`, and cost per decision at `0.12`.

- [ ] **Step 5: Run the agent integration test and `glassbox eval --suite goldens/replenishment_triage/manifest.yaml`; expect exit 0. Commit in the agent repository:**

```bash
git add requirements-eval.txt integrations goldens schemas tests/test_replenishment_evaluation.py
git commit -m "feat: add replenishment evaluation adapter"
```

### Task 6: Complete golden suite, documentation, and acceptance evidence

**Files:**
- Modify: `README.md`
- Modify: `TODO.md`
- Modify: `docs/superpowers/specs/2026-08-30-glassbox-p1-evaluation-design.md` only to record measured evidence, if needed
- Modify in agent repo: `goldens/replenishment_triage/manifest.yaml` and `goldens/replenishment_triage/cases/*.yaml`

**Interfaces:**
- Consumes the runnable seed suite from Task 5.
- Produces a 40-case balanced suite and documented P1 evidence.

- [ ] **Step 1: Add 38 readable one-exception cases to the two seed cases: 16 routine, 12 genuinely ambiguous, 8 adversarial (missing, stale, or contradictory data), and 4 do-nothing total.** Every case declares the expected urgency and provider fixture response; every do-nothing case must include three genuine signals and one rejected action, not filler.

- [ ] **Step 2: Write/extend a manifest-integrity test that asserts exactly 40 unique IDs and the `16/12/8/4` category distribution.** Assert every case resolves its local schema, target, and required fields.

- [ ] **Step 3: Run the 40-case evaluation.** Record the per-assertion results, linear weighted kappa, confusion matrix, p50/p95 latency, cost, token efficiency, and error rate. Inspect the four do-nothing cases for evidentiary padding.

- [ ] **Step 4: If the source 0.6 urgency gate or a do-nothing case fails, do not weaken a category rule.** Correct the scripted response, domain mapping, or documented agent reasoning requirement; record any approved threshold change in `TODO.md` with the measured linear-kappa evidence.

- [ ] **Step 5: Add the README command and final checks.** Document:

```shell
python -m pip install -r requirements-eval.txt
glassbox eval --suite goldens/replenishment_triage/manifest.yaml
```

Run `pytest -q`, `ruff check .`, `mypy glassbox`, `lint-imports`, the agent's evaluation test, and the 40-case CLI suite. Mark only evidence-backed P1 TODO items complete. Commit Glassbox documentation separately from the agent golden-suite commit.

## Plan Self-Review

- **Spec coverage:** Tasks 1–3 implement generic contracts, runner, CLI, checks, metrics, gates, and CI import boundaries. Tasks 4–5 implement optional fallback-safe real-agent instrumentation and the agent-owned adapter. Task 6 implements the 40-case composition, calibration check, do-nothing inspection, and documentation.
- **No placeholders:** Every task names files, interfaces, test behavior, commands, and a commit boundary. The two explicitly deferred items—live provider and post-seed domain mapping—remain in `TODO.md`, not as omitted implementation steps.
- **Type consistency:** All target paths expose `run_case(case: GoldenCase) -> DecisionResult`; Glassbox never imports agent classes; the agent is the only owner of its adapter, manifest, and cases.
