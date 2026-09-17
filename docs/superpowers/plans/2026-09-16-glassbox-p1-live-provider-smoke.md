# Glassbox P1 Live-Provider Smoke Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the replenishment agent's one-case live-provider probe with an explicit, bounded five-case smoke report that reuses Glassbox evaluation contracts without entering CI.

**Architecture:** The agent-owned `scripts/run_live_eval_smoke.py` stays the only live-provider entry point. It loads five committed existing golden cases, runs each through the real `TriageAgent` using the agent's normal provider configuration, reads persisted Glassbox telemetry into `DecisionResult`, and evaluates the same four structural assertions. It emits canonical JSON and uses a `null` value for billed-cost metrics because the provider response interface exposes tokens but not cost.

**Tech Stack:** Python 3.11, argparse, PyYAML, Pydantic, existing Glassbox `DecisionResult` / assertions, pytest.

## Global Constraints

- Work in `/Users/MKunta/AGENTS/CODE/AI-driven-replenishment-exception-triage-agent`; do not add a Glassbox provider SDK, provider credentials, or provider configuration.
- A live run must refuse before configuration loading unless `LIVE_EVAL_ENABLED=1`.
- The profile contains exactly five committed existing case files: routine `critical_oos.yaml`, ambiguous `ambiguous_supply_001.yaml`, adversarial `adversarial_missing_001.yaml`, and do-nothing `do_nothing.yaml` plus `low_monitor_002.yaml`.
- Use the existing `AGENT_PROVIDER`, `AGENT_MODEL`, and provider credential flow through `load_config` / `validate_required_env_vars`.
- Run cases sequentially; each case gets an isolated temporary SQLite database and one Glassbox trace lifecycle.
- Load and validate provider configuration once per run, share that immutable configuration across all five cases, report a per-case error type only, and continue. Exit `1` when any case errors or deterministic assertion fails; setup/argument failures use argparse's exit `2`.
- Output is a single `json.dumps(..., sort_keys=True, separators=(",", ":"))` document. `total_cost_usd` and `cost_per_decision` are JSON `null`, never `0.0`.
- The command stays outside `glassbox eval`, suite manifests, GitHub CI, and deterministic merge gates.

---

## File structure

```text
AI-driven-replenishment-exception-triage-agent/
  scripts/run_live_eval_smoke.py          # opted-in profile, live execution, reporting
  tests/test_live_eval_smoke.py           # isolated no-network smoke-script tests
  README.md                               # explicit manual command and non-gating policy
```

### Task 1: Add a testable live-smoke profile and report contract

**Files:**
- Create: `tests/test_live_eval_smoke.py`
- Modify: `scripts/run_live_eval_smoke.py`

**Interfaces:**
- Consumes: existing golden files under `goldens/replenishment_triage/cases/`; `GoldenCase`; `DecisionResult`; `evaluate_deterministic`.
- Produces: `SMOKE_CASE_FILES: tuple[str, ...]`, `load_smoke_cases(root: Path) -> tuple[GoldenCase, ...]`, `run_smoke(cases: tuple[GoldenCase, ...], *, config: AppConfig) -> dict[str, object]`, and `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Write failing profile and canonical-report tests**

```python
# tests/test_live_eval_smoke.py
import json
from pathlib import Path

import pytest

from scripts import run_live_eval_smoke as smoke


def test_live_smoke_refuses_before_loading_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIVE_EVAL_ENABLED", raising=False)
    monkeypatch.setattr(smoke, "load_config", lambda _: pytest.fail("loaded config"))

    with pytest.raises(SystemExit) as raised:
        smoke.main([])

    assert raised.value.code == 2


def test_profile_is_fixed_and_covers_each_required_category() -> None:
    cases = smoke.load_smoke_cases(Path.cwd())

    assert tuple(case.case_id for case in cases) == (
        "critical-oos-001", "ambiguous-supply-001", "adversarial-missing-001",
        "low-monitor-001", "low-monitor-002",
    )
    assert [case.metadata["category"] for case in cases] == [
        "routine", "ambiguous", "adversarial", "do_nothing", "do_nothing"
    ]


def test_report_is_canonical_and_marks_cost_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    cases = smoke.load_smoke_cases(Path.cwd())
    monkeypatch.setattr(smoke, "run_live_case", lambda case, config: smoke.DecisionResult(
        decision={"urgency": "LOW", "action": "Do nothing"}, evidence=(),
        rationale_citations=(), alternatives_considered=(),
        measurements={"latency_ms": 12.0, "tokens": 7},
    ))

    report = smoke.run_smoke(cases, config=object())

    assert [item["case_id"] for item in report["cases"]] == [case.case_id for case in cases]
    assert report["metrics"]["total_cost_usd"] is None
    assert json.dumps(report, sort_keys=True, separators=(",", ":")) == smoke.canonical_json(report)
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run: `.venv/bin/pytest tests/test_live_eval_smoke.py -v`

Expected: FAIL because `load_smoke_cases`, `run_smoke`, and `canonical_json` do not exist and the legacy command requires `--case`.

- [ ] **Step 3: Implement the fixed profile and pure reporting helpers**

Replace the `--case` argument with no required case argument. Define the fixed relative paths and path-safe loader:

```python
SMOKE_CASE_FILES = (
    "goldens/replenishment_triage/cases/critical_oos.yaml",
    "goldens/replenishment_triage/cases/ambiguous_supply_001.yaml",
    "goldens/replenishment_triage/cases/adversarial_missing_001.yaml",
    "goldens/replenishment_triage/cases/do_nothing.yaml",
    "goldens/replenishment_triage/cases/low_monitor_002.yaml",
)


def load_smoke_cases(root: Path) -> tuple[GoldenCase, ...]:
    return tuple(
        GoldenCase.model_validate(yaml.safe_load((root / relative).read_text()))
        for relative in SMOKE_CASE_FILES
    )


def canonical_json(report: dict[str, object]) -> str:
    return json.dumps(report, sort_keys=True, separators=(",", ":"))
```

Add `run_smoke` so it loads the existing recommendation schema once, calls
`run_live_case` once per case, catches each exception into
`{"type": type(exc).__name__}`, calls
`evaluate_deterministic` only for successful results, and returns this shape:

```python
{
    "cases": [
        {"case_id": case.case_id, "checks": [check.model_dump() for check in checks], "error": error}
    ],
    "metrics": {
        "p50_latency_ms": p50,
        "p95_latency_ms": p95,
        "total_tokens": total_tokens,
        "tokens_per_decision": tokens_per_case,
        "error_rate": error_rate,
        "total_cost_usd": None,
        "cost_per_decision": None,
    },
}
```

Use a small local `_percentile` helper or `glassbox.eval.metrics.operational_metrics`
only for latency/token fields; do not pass a fabricated `cost_usd: 0.0` value.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `.venv/bin/pytest tests/test_live_eval_smoke.py -v`

Expected: PASS.

- [ ] **Step 5: Commit the first deliverable**

```bash
git add scripts/run_live_eval_smoke.py tests/test_live_eval_smoke.py
git commit -m "feat: add bounded live evaluation smoke profile"
```

### Task 2: Preserve real-agent execution and continuation semantics

**Files:**
- Modify: `scripts/run_live_eval_smoke.py`
- Modify: `tests/test_live_eval_smoke.py`

**Interfaces:**
- Consumes: `GoldenCase`, one validated `AppConfig` from `load_config`, the real `TriageAgent`, and persisted `Repository.trace_tree` decision records.
- Produces: `run_live_case(case: GoldenCase, *, config: AppConfig) -> DecisionResult`; `main` prints the canonical report and returns `0` only when every case is structurally valid and error-free.

- [ ] **Step 1: Add failing continuation and status tests**

```python
def test_smoke_continues_after_one_case_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    cases = smoke.load_smoke_cases(Path.cwd())
    calls: list[str] = []

    def fake_live_case(case: smoke.GoldenCase, *, config: object) -> smoke.DecisionResult:
        calls.append(case.case_id)
        if case.case_id == "ambiguous-supply-001":
            raise RuntimeError("provider unavailable")
        return smoke.DecisionResult(
            decision={"urgency": "LOW", "action": "Do nothing"}, evidence=(),
            rationale_citations=(), alternatives_considered=(),
        )

    monkeypatch.setattr(smoke, "run_live_case", fake_live_case)
    report = smoke.run_smoke(cases, config=object())

    assert calls == [case.case_id for case in cases]
    assert report["cases"][1]["error"] == {"type": "RuntimeError"}


def test_main_returns_one_when_a_reported_case_is_not_clean(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LIVE_EVAL_ENABLED", "1")
    monkeypatch.setattr(smoke, "load_config", lambda _: object())
    monkeypatch.setattr(smoke, "validate_required_env_vars", lambda _: None)
    monkeypatch.setattr(smoke, "load_smoke_cases", lambda _: ())
    monkeypatch.setattr(smoke, "run_smoke", lambda cases, config: {
        "cases": [{"case_id": "x", "checks": [], "error": {"type": "RuntimeError"}}],
        "metrics": {"total_cost_usd": None, "cost_per_decision": None},
    })

    assert smoke.main([]) == 1
    assert json.loads(capsys.readouterr().out)["cases"][0]["case_id"] == "x"
```

- [ ] **Step 2: Run focused tests to verify they fail**

Run: `.venv/bin/pytest tests/test_live_eval_smoke.py -v`

Expected: FAIL because the legacy execution returns an opaque recommendation
instead of a `DecisionResult`, aborts on the first exception, and always
returns `0` after one run.

- [ ] **Step 3: Extract the existing live execution into `run_live_case`**

Move the current real `TriageAgent(config).run([exception])` lifecycle into:

```python
def run_live_case(case: GoldenCase, *, config: AppConfig) -> DecisionResult:
    exception = EnrichedExceptionSchema.model_validate(case.input)
    agent = TriageAgent(config)
    with tempfile.TemporaryDirectory() as directory:
        database = Database.open(Path(directory) / "live-evaluation.sqlite3")
        collector = Collector(Repository(database))
        gb.init(agent="replenishment-triage", version="live-evaluation", collector=collector)
        try:
            started_at = perf_counter()
            run_result = agent.run([exception])
            if not gb.flush(timeout=30.0):
                raise RuntimeError("Glassbox collector did not flush within 30 seconds")
            trace_id = database.connection.execute("SELECT trace_id FROM traces").fetchone()[0]
            stored = Repository(database).trace_tree(trace_id).decisions[0]
        finally:
            gb.shutdown(timeout=5.0)
            database.close()
    return DecisionResult(
        decision=dict(stored.event.recommendation),
        evidence=tuple(EvidenceRecord(evidence_id=item.evidence_id, fields={item.field_name: item.field_value}) for item in stored.evidence),
        rationale_citations=stored.event.rationale_citations,
        alternatives_considered=stored.event.alternatives_considered,
        measurements={"latency_ms": (perf_counter() - started_at) * 1_000, "tokens": run_result.statistics.total_input_tokens + run_result.statistics.total_output_tokens},
    )
```

Call `config = load_config(arguments.config)` and
`validate_required_env_vars(config)` once in `main`, only after the opt-in
check, then call `run_smoke(cases, config=config)`. Print `canonical_json(report)`. Return `1` when
any `error` is non-null or any check is false; otherwise return `0`.

- [ ] **Step 4: Run the focused smoke-script tests**

Run: `.venv/bin/pytest tests/test_live_eval_smoke.py -v`

Expected: PASS with no provider network call because every real execution is
replaced by a test double.

- [ ] **Step 5: Commit the execution behavior**

```bash
git add scripts/run_live_eval_smoke.py tests/test_live_eval_smoke.py
git commit -m "feat: report live evaluation smoke outcomes"
```

### Task 3: Document manual execution and prove deterministic isolation

**Files:**
- Modify: `README.md`
- Modify: `tests/test_live_eval_smoke.py`

**Interfaces:**
- Consumes: `scripts/run_live_eval_smoke.py` with no user-supplied case argument.
- Produces: a safe, copyable manual smoke command and regression coverage that CI-facing deterministic evaluation is untouched.

- [ ] **Step 1: Add failing documentation/isolation tests**

```python
def test_live_smoke_script_is_not_a_manifest_target() -> None:
    manifest = Path("goldens/replenishment_triage/manifest.yaml").read_text()
    assert "run_live_eval_smoke" not in manifest
    assert "LIVE_EVAL_ENABLED" not in manifest
```

Add an assertion that the README contains both the exact opt-in variable and
the script command.

- [ ] **Step 2: Run focused tests to verify the README assertion fails**

Run: `.venv/bin/pytest tests/test_live_eval_smoke.py -v`

Expected: FAIL because the README has no live-evaluation smoke section.

- [ ] **Step 3: Add concise README guidance**

Under a new **Run the optional live-provider Glassbox smoke** subsection, add:

```bash
# Uses the configured AGENT_PROVIDER / AGENT_MODEL and its existing credential.
# This invokes five real model calls, is not part of CI, and reports unavailable
# billed cost as null because providers expose token counts but not billed cost.
LIVE_EVAL_ENABLED=1 .venv/bin/python scripts/run_live_eval_smoke.py
```

State that nonzero means one or more live cases errored or failed a structural
assertion; it does not change deterministic suite gates.

- [ ] **Step 4: Run focused tests and the existing deterministic evaluation suite**

Run:

```bash
.venv/bin/pytest tests/test_live_eval_smoke.py tests/test_replenishment_evaluation.py -v
.venv/bin/python -m glassbox.cli eval --suite goldens/replenishment_triage/manifest.yaml
```

Expected: all tests pass; deterministic command exits `0` without requiring
`LIVE_EVAL_ENABLED` or any provider credential.

- [ ] **Step 5: Commit documentation and isolation proof**

```bash
git add README.md tests/test_live_eval_smoke.py
git commit -m "docs: explain optional live evaluation smoke"
```

## Final verification

- [ ] Run `.venv/bin/pytest tests/test_live_eval_smoke.py tests/test_replenishment_evaluation.py -v` and expect success.
- [ ] Run `.venv/bin/python -m glassbox.cli eval --suite goldens/replenishment_triage/manifest.yaml` with no `LIVE_EVAL_ENABLED` and no provider credential; expect exit `0`.
- [ ] Run `.venv/bin/python scripts/run_live_eval_smoke.py` with `LIVE_EVAL_ENABLED` absent; expect exit `2` before provider configuration is loaded.
- [ ] With intentionally configured non-production provider credentials, run `LIVE_EVAL_ENABLED=1 .venv/bin/python scripts/run_live_eval_smoke.py`; inspect one canonical JSON document with five case IDs, per-case checks/errors, token/latency metrics, and `null` cost metrics. This manual command is the only step allowed to contact a provider.

## Plan self-review

- **Spec coverage:** Task 1 implements fixed selection, canonical reporting, structural assertions, and unavailable-cost semantics. Task 2 implements real provider execution, per-case isolation, continuation, and exit status. Task 3 documents opt-in use and proves the deterministic manifest/CLI stays isolated.
- **No placeholders:** Every task names exact agent-repository files, selected case files, test behavior, command, and commit boundary.
- **Type consistency:** `run_live_case` always returns `DecisionResult` from the one validated `AppConfig`; `run_smoke` owns report creation; `main` owns environment/config gating and process status. The report's nullable cost fields remain outside `DecisionResult.measurements`, whose contract accepts only numeric values. Per-case errors carry only exception types, so provider exception text cannot leak into reports.
