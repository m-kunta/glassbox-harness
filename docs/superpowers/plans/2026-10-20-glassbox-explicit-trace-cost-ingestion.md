# Explicit Trace Cost Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an instrumented agent explicitly record a known total USD cost on its active root trace without estimating prices or changing fail-open behavior.

**Architecture:** A public SDK helper updates the immutable context-local `TraceState`. The existing root-trace completion path reads that state and emits `TraceEvent.total_cost_usd` only when the caller set it. Existing repository upsert semantics then preserve `NULL` for unknown values and persist explicitly supplied totals without a schema change.

**Tech Stack:** Python 3.11, `contextvars`, frozen dataclasses, Pydantic event models, SQLite/WAL, pytest, Ruff, mypy, import-linter.

## Global Constraints

- `set_trace_cost_usd(total_cost_usd: float) -> None` records a complete trace total, never a per-call increment.
- Only finite, non-negative real values are accepted; booleans, strings, NaN, infinity, and negatives are ignored silently.
- A later valid call replaces the earlier total in the same trace.
- No active trace, disabled tracing, and invalid input are all no-ops and must not change the agent's return value or exception behavior.
- `0.0` is persisted only when explicitly supplied; omitted cost remains `NULL`.
- Do not add provider SDKs, pricing tables, token-cost estimation, migrations, or policy changes. `trace_cost_usd` remains disabled in the shipped drift policy.
- New code follows TDD: run each focused test red before the production change and preserve the existing SDK/store/import boundaries.

---

## File structure

| Path | Responsibility |
| --- | --- |
| `glassbox/sdk/context.py` | Stores the optional explicit trace total in immutable context-local trace state. |
| `glassbox/sdk/tracer.py` | Validates the public input, updates the active trace state, and emits the supplied total at root-trace completion. |
| `glassbox/sdk/__init__.py` | Re-exports the SDK helper. |
| `glassbox/__init__.py` | Re-exports the helper from the top-level public Glassbox API. |
| `tests/sdk/test_tracer.py` | Proves event-level state, replacement, invalid-input, disabled, and exception-preservation behavior. |
| `tests/store/test_repository.py` | Proves an explicitly completed trace cost reaches the existing SQLite trace row while an omitted cost remains null. |
| `README.md` | Documents provider-neutral, explicit cost capture and the fact that cost drift remains opt-in. |

### Task 1: Add the fail-open SDK cost contract

**Files:**
- Modify: `glassbox/sdk/context.py:14-24`
- Modify: `glassbox/sdk/tracer.py:1-90, 286-322, 341`
- Modify: `glassbox/sdk/__init__.py:3-15`
- Modify: `glassbox/__init__.py:3-19`
- Modify: `tests/sdk/test_tracer.py`
- Modify: `tests/store/test_repository.py`

**Interfaces:**
- Consumes: `TraceState`, `current_trace`, and `set_trace` from `glassbox.sdk.context`; `TraceEvent` from `glassbox.events`.
- Produces: `glassbox.set_trace_cost_usd(total_cost_usd: float) -> None` and the identical `glassbox.sdk.set_trace_cost_usd` re-export.
- Contract: the completed root `TraceEvent` contains `total_cost_usd` exactly when the active trace received a valid call.

- [ ] **Step 1: Write focused failing SDK tests.**

  Add these tests to `tests/sdk/test_tracer.py`, reusing its `RecordingCollector`, `gb.init(...)`, and `reset_for_testing()` cleanup pattern:

  ```python
  def test_explicit_trace_cost_is_emitted_only_when_the_trace_completes(monkeypatch):
      start = datetime(2026, 10, 20, tzinfo=UTC)
      end = start + timedelta(milliseconds=1)
      monkeypatch.setattr(tracer, "_now", lambda: next(iter((start, end))))
      collector = RecordingCollector()
      gb.init(agent="test", version="test", collector=collector)

      @gb.trace
      def workload() -> None:
          gb.set_trace_cost_usd(0.0125)

      try:
          workload()
          initial, completed = [event for event in collector.events if isinstance(event, TraceEvent)]
          assert initial.total_cost_usd is None
          assert completed.total_cost_usd == 0.0125
      finally:
          reset_for_testing()
  ```

  Use a two-value iterator defined outside the lambda rather than constructing it inside the lambda. Add a replacement test that calls `gb.set_trace_cost_usd(0.01)` and then `gb.set_trace_cost_usd(0.02)` inside one trace and asserts `0.02` on completion. Add a parameterized no-op test over `-0.01`, `float("nan")`, `float("inf")`, `"0.01"`, and `True`; it must assert no exception and a null terminal cost. Add a no-active-trace call and a disabled-tracing call, each asserting no emitted cost event. Add a top-level API check:

  ```python
  def test_cost_helper_is_reexported_from_both_public_surfaces():
      assert gb.set_trace_cost_usd is tracer.set_trace_cost_usd
      from glassbox import sdk
      assert sdk.set_trace_cost_usd is tracer.set_trace_cost_usd
  ```

  Finally, make the traced workload set a valid total and then raise `RuntimeError`; assert the original exception still escapes and the completed error trace retains the total.

  In `tests/store/test_repository.py`, add a regression that persists two completed
  trace events through the normal repository event path: one explicit
  `total_cost_usd=0.0`, one with the default field omitted. Query the two rows and
  assert exactly:

  ```python
  assert {row["trace_id"]: row["total_cost_usd"] for row in rows} == {
      explicit_zero.trace_id: 0.0,
      omitted.trace_id: None,
  }
  ```

  Use distinct canonical ULIDs and write each required start event before its
  completion. This demonstrates that explicit zero and unknown cost are distinct
  even though no repository change is expected.

- [ ] **Step 2: Run the focused tests to verify they fail.**

  Run:

  ```shell
  .venv/bin/pytest tests/sdk/test_tracer.py -q
  .venv/bin/pytest tests/store/test_repository.py -q
  ```

  Expected: SDK failures because `glassbox.set_trace_cost_usd` and
  `TraceState.total_cost_usd` do not exist; the repository assertion provides a
  regression guard for the event shape that Task 1 will add.

- [ ] **Step 3: Add state, validation, emission, and public exports.**

  In `glassbox/sdk/context.py`, add the optional field to the frozen state:

  ```python
  @dataclass(frozen=True)
  class TraceState:
      # existing identity fields unchanged
      input_capture: Future[str | None] | None = None
      total_cost_usd: float | None = None
  ```

  In `glassbox/sdk/tracer.py`, import `math` and `Real`, then add this helper next to `capture_input`:

  ```python
  def set_trace_cost_usd(total_cost_usd: float) -> None:
      """Record an explicit total USD cost for the active root trace, fail-open."""
      if (
          isinstance(total_cost_usd, bool)
          or not isinstance(total_cost_usd, Real)
          or not math.isfinite(total_cost_usd)
          or total_cost_usd < 0
      ):
          return
      trace_state = current_trace.get()
      if trace_state is not None and get_config().enabled:
          set_trace(replace(trace_state, total_cost_usd=float(total_cost_usd)))
  ```

  In `_emit_trace`, immediately after adding the completed `latency_ms`, conditionally add the supplied total:

  ```python
  if state.total_cost_usd is not None:
      values["total_cost_usd"] = state.total_cost_usd
  ```

  Do not add the field for start events. Add `set_trace_cost_usd` to `tracer.__all__`, import/re-export it from `glassbox/sdk/__init__.py`, and import/re-export it from `glassbox/__init__.py`.

- [ ] **Step 4: Run focused SDK checks green.**

  Run:

  ```shell
  .venv/bin/pytest tests/sdk/test_tracer.py -q
  .venv/bin/pytest tests/store/test_repository.py -q
  .venv/bin/ruff check glassbox/sdk/context.py glassbox/sdk/tracer.py glassbox/sdk/__init__.py glassbox/__init__.py tests/sdk/test_tracer.py
  .venv/bin/mypy glassbox/sdk/context.py glassbox/sdk/tracer.py glassbox/sdk/__init__.py glassbox/__init__.py
  ```

  Expected: all tracer tests pass, invalid values do not escape, and strict typing accepts the public signature.

- [ ] **Step 5: Commit the SDK contract.**

  ```shell
  git add glassbox/sdk/context.py glassbox/sdk/tracer.py glassbox/sdk/__init__.py glassbox/__init__.py tests/sdk/test_tracer.py tests/store/test_repository.py
  git commit -m "feat: add explicit trace cost capture"
  ```

### Task 2: Document intentional monitoring scope

**Files:**
- Modify: `README.md:45-55, 210-214`

**Interfaces:**
- Consumes: the public `TraceEvent(total_cost_usd=...)` schema and `Repository._write_trace` optional-column behavior verified in Task 1.
- Produces: clear operator-facing usage guidance.

- [ ] **Step 1: Confirm no repository change is needed and add precise documentation.**

  Do not modify `glassbox/store/repository.py`: its `_write_trace` method already uses `event.model_fields_set` to distinguish explicitly supplied optional fields from absent ones.

  Extend the SDK quick-start after `glassbox.capture_input(request_payload)`:

  ```python
  # After the provider reports its own known total for this trace:
  glassbox.set_trace_cost_usd(provider_response.total_cost_usd)
  ```

  Add text immediately below it: Glassbox records a caller-supplied total only; it does not estimate prices from model or token metadata. Omit the call when the provider does not expose a trusted total.

  Replace README’s current “Cost ingestion is deferred” sentence with: cost capture is available only through `set_trace_cost_usd`; the default drift policy still leaves trace cost disabled, so enabling it later requires a versioned policy change and a new baseline.

- [ ] **Step 2: Run full SDK and quality checks.**

  Run:

  ```shell
  .venv/bin/pytest tests/sdk tests/store/test_repository.py -q
  .venv/bin/ruff check glassbox tests
  .venv/bin/mypy glassbox
  .venv/bin/lint-imports
  ```

  Expected: explicit non-negative values (including zero) persist, omitted values stay null, and all architecture contracts hold.

- [ ] **Step 3: Commit documentation.**

  ```shell
  git add README.md
  git commit -m "docs: explain explicit trace cost capture"
  ```

### Task 3: Final integration verification and roadmap update

**Files:**
- Modify: `TODO.md:101`

**Interfaces:**
- Consumes: the completed public SDK contract and persistence tests from Tasks 1–2.
- Produces: an accurate roadmap status; no source-code interface changes.

- [ ] **Step 1: Update the fulfilled backlog item.**

  Change only the P3 cost item from:

  ```markdown
  - [ ] Add explicit cost ingestion before enabling the optional `trace_cost_usd` drift signal; preserve null costs as missing observations.
  ```

  to:

  ```markdown
  - [x] Add explicit cost ingestion; preserve null costs as missing observations. `trace_cost_usd` remains disabled until a versioned policy enables it and a new baseline is created.
  ```

- [ ] **Step 2: Run the whole project test suite.**

  Run:

  ```shell
  .venv/bin/pytest -q
  .venv/bin/ruff check .
  .venv/bin/mypy glassbox
  .venv/bin/lint-imports
  git diff --check
  git status --short
  ```

  Expected: all tests pass; lint, typing, import contracts, and whitespace checks are clean. Do not stage or delete unrelated untracked `.superpowers/` files.

- [ ] **Step 3: Commit the roadmap record and push the complete series.**

  ```shell
  git add TODO.md
  git commit -m "docs: mark trace cost ingestion complete"
  git push origin main
  ```

  Expected: only the three task commits are pushed; no force push and no unrelated workspace notes are included.

## Plan self-review

- **Spec coverage:** Task 1 implements the complete-total, last-valid-write-wins, public-export, finite/non-negative, inactive/disabled, and fail-open rules. Task 2 proves explicit-versus-unknown persistence and updates operator documentation while preserving the disabled default policy. Task 3 records completion and applies full verification.
- **No placeholders:** Each production step names the exact symbols, fields, file paths, commands, and test assertions needed to perform it. The repository non-change is explicit because existing `model_fields_set` behavior is the intended persistence mechanism.
- **Type consistency:** `TraceState.total_cost_usd`, `set_trace_cost_usd`, `TraceEvent.total_cost_usd`, and the two public re-exports use the same exact names throughout.
