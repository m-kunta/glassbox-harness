# Static Operations Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `glassbox dashboard`, a safe, self-contained HTML snapshot of drift, outcome reconciliation, and bounded counterfactual-run provenance.

**Architecture:** `glassbox.dashboard` opens one strict read-only database connection, begins one explicit read transaction, and assembles typed presentation models from existing drift/reconciliation contracts plus a new bounded counterfactual-run projection. It renders one Jinja template with inline existing CSS and publishes it through an atomic writer. `glassbox.cli` lazily imports this boundary only for the new command.

**Tech Stack:** Python 3.11+, SQLite read-only/WAL connection, existing Pydantic/dataclass report contracts, Jinja2 autoescaping, pytest, Ruff, mypy, import-linter.

## Global Constraints

- Add `glassbox dashboard --output <path>`; the existing global `--database` default remains `GLASSBOX_DATABASE` or `glassbox.sqlite3`.
- Open the database exactly once with `Database.open_read_only()`, issue `BEGIN` before source reads, and use one captured UTC `as_of` value for all drift reports.
- Never initialize/migrate/write SQLite, persist a drift run, claim/send a Discord delivery, inspect `launchctl`, load a provider, or call a network service.
- Render only drift/reconciliation/counterfactual run metadata. Exclude Decision Cards, traces, evidence, prompts, completions, blobs, feedback, overrides, provider base URLs, credentials, and live links.
- Use Jinja autoescaping, inline CSS, no JavaScript/forms/external URLs, and generic safe command errors. No output path, SQLite error, secret, or persisted content may reach stderr.
- Output uses UTF-8, same-directory private temporary creation, fsync, and atomic replace; existing output requires `--overwrite`.
- Show normal empty, `insufficient_data`, disabled-signal, and bounded-list states faithfully; convert known per-section report/policy/data-integrity failures into an `Unavailable` section without exception detail.
- Preserve architecture direction: `cli → dashboard → store/eval/read_models`; dashboard must not import FastAPI/server/auth, SDK, collector, provider adapters, Discord transport/config, or scheduler.
- Every task is test-first, individually committed and pushed after review. Do not modify the P2.4 usability item or enable trace-latency monitoring.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `glassbox/store/repository.py` | Read a bounded, timestamp-ordered projection of persisted counterfactual run headers only. |
| `tests/store/test_repository.py` | Prove projection ordering, cap behavior, and that run headers—not result payloads—are returned. |
| `glassbox/dashboard.py` | Own the single-connection static snapshot, typed dashboard presentation models, section-level safety, rendering, and atomic publication. |
| `glassbox/web/templates/dashboard.html` | Render typed dashboard display models into a standalone document. |
| `glassbox/web/static/glassbox.css` | Add only reusable responsive dashboard layout styles. |
| `tests/test_dashboard.py` | Test snapshot consistency, content boundaries, escaping, standalone output, normal/error states, and writer safety. |
| `glassbox/cli.py` | Parse and lazily dispatch `dashboard`; preserve safe exit behavior. |
| `tests/test_cli.py` | Exercise command success, generic failure, no-write behavior, and lazy dashboard import boundary. |
| `pyproject.toml` / `tests/test_architecture.py` | Add and test dashboard import-linter isolation. |
| `README.md` / `TODO.md` | Document command/scope and mark only dashboard generation complete. |

## Task 1: Bounded Counterfactual Run Projection

**Files:**
- Modify: `glassbox/store/repository.py`
- Modify: `tests/store/test_repository.py`

**Interfaces:**
- Consumes: existing immutable `CounterfactualRun` dataclass and the `counterfactual_runs` table.
- Produces: `Repository.counterfactual_runs(limit: int) -> tuple[CounterfactualRun, ...]`, ordered newest-first by `glassbox_timestamp_key(created_at) DESC, run_id DESC` and containing no `counterfactual_results` values.

- [ ] **Step 1: Write failing repository tests for order, bound, and header-only access**

  Add tests that persist three runs with equal/mixed timestamp precision and private result rationale values, then call the new projection:

  ```python
  def test_counterfactual_runs_are_newest_first_with_ulid_tie_breaker(repository):
      repository.record_counterfactual_run(_run("01ARZ3NDEKTSV4RRFFQ69G5FA2", created_at=_at("2026-10-01T12:00:00Z")), ())
      repository.record_counterfactual_run(_run("01ARZ3NDEKTSV4RRFFQ69G5FA3", created_at=_at("2026-10-01T12:00:00.001Z")), ())
      repository.record_counterfactual_run(_run("01ARZ3NDEKTSV4RRFFQ69G5FA4", created_at=_at("2026-10-01T12:00:00.001Z")), ())

      assert [run.run_id for run in repository.counterfactual_runs(3)] == [
          "01ARZ3NDEKTSV4RRFFQ69G5FA4",
          "01ARZ3NDEKTSV4RRFFQ69G5FA3",
          "01ARZ3NDEKTSV4RRFFQ69G5FA2",
      ]
  ```

  Add a `limit=2` assertion and `pytest.raises(ValueError)` for `0`, `-1`, and a boolean limit. Add a run with a result whose rationale is `PRIVATE_COUNTERFACTUAL_RATIONALE`, then assert the returned run dataclass has no result collection attribute and `repr(runs)` does not contain that sentinel.

- [ ] **Step 2: Run the focused test to verify it fails**

  Run: `pytest tests/store/test_repository.py -k counterfactual_runs -v`  
  Expected: FAIL because `Repository.counterfactual_runs` does not exist.

- [ ] **Step 3: Implement the bounded header query**

  Add this method beside `counterfactual_run_detail`:

  ```python
  def counterfactual_runs(self, limit: int) -> tuple[CounterfactualRun, ...]:
      if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
          raise ValueError("counterfactual run limit is invalid")
      with self._operation_lock:
          rows = self._connection.execute(
              "SELECT * FROM counterfactual_runs "
              "ORDER BY glassbox_timestamp_key(created_at) DESC, run_id DESC LIMIT ?",
              (limit,),
          ).fetchall()
      runs: list[CounterfactualRun] = []
      for row in rows:
          values = dict(row)
          for name in ("created_at", "as_of", "decided_since"):
              values[name] = datetime.fromisoformat(values[name].replace("Z", "+00:00"))
          values["remote_egress_confirmed"] = bool(values["remote_egress_confirmed"])
          runs.append(CounterfactualRun(**values))
      return tuple(runs)
  ```

  Do not join `counterfactual_results`, parse result JSON, or expose `base_url` through a dashboard view.

- [ ] **Step 4: Run focused repository verification**

  Run: `pytest tests/store/test_repository.py -k counterfactual -v`  
  Expected: PASS, including existing immutable-run tests.

- [ ] **Step 5: Commit and push the independently verified projection**

  ```bash
  git add glassbox/store/repository.py tests/store/test_repository.py
  git commit -m "feat: list bounded counterfactual run headers"
  git push origin main
  ```

### Task 2: Typed Read-Only Dashboard Snapshot

**Files:**
- Create: `glassbox/dashboard.py`
- Create: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `Database.open_read_only`, `Repository.drift_agent_names`, `Repository.reconciliation_source`, `Repository.counterfactual_runs`, `run_live_drift_report`, `calculate_report`, `load_policy`, `build_drift_report_view`, and `CounterfactualRun`.
- Produces: `build_dashboard(path: Path, *, clock: Callable[[], datetime]) -> DashboardView`; `DashboardView` has `generated_at`, `drift`, `reconciliation`, and `counterfactuals` fields; `render_dashboard(view: DashboardView) -> str` is added in Task 3.

- [ ] **Step 1: Write failing snapshot tests**

  Create fixtures that seed two agent cohorts, an immutable drift baseline, mature labelled outcomes, and 21 counterfactual runs. Write tests for:

  ```python
  def test_dashboard_uses_one_as_of_and_orders_cohorts_and_runs(tmp_path):
      view = build_dashboard(path, clock=lambda: datetime(2026, 11, 6, 12, tzinfo=UTC))

      assert view.generated_at == "2026-11-06T12:00:00Z"
      assert [item.agent_name for item in view.drift.cohorts] == ["agent-a", "agent-b"]
      assert len(view.counterfactuals.runs) == 20
      assert view.counterfactuals.has_older_runs is True
  ```

  Add tests that monkeypatch `Repository.record_drift_run`, `Repository.claim_discord_delivery`, scheduler functions, and provider factories to raise if called; `build_dashboard()` must still succeed. Add a test that forces `run_live_drift_report` to raise `DriftPolicyError` and asserts `view.drift.state == "unavailable"` with no exception text. Add normal empty-state tests for no cohorts, no outcomes, and no counterfactual runs. Add reconciliation tests asserting the v2 policy hash/version and decision-type rows are sorted.

- [ ] **Step 2: Run the new snapshot tests to verify they fail**

  Run: `pytest tests/test_dashboard.py -v`  
  Expected: FAIL because `glassbox.dashboard` does not exist.

- [ ] **Step 3: Implement focused typed models and snapshot assembly**

  In `glassbox/dashboard.py`, define immutable display dataclasses such as:

  ```python
  @dataclass(frozen=True)
  class DashboardSection:
      state: Literal["ready", "empty", "unavailable"]
      message: str

  @dataclass(frozen=True)
  class CounterfactualRunView:
      run_id: str
      created_at: str
      provider: str
      model: str
      prompt_version: str
      prompt_hash: str
      execution_mode: str
      selected_count: int
      succeeded_count: int
      failed_count: int
      outcome_policy_version: str
      outcome_policy_hash: str
  ```

  Capture `as_of = clock()` and validate UTC. Open exactly one strict read-only database, execute `BEGIN`, create one `Repository`, and build all sections before `finally: database.close()`. Load drift policy via its existing checked-in default and load reconciliation specifically from `glassbox/eval/policies/reconciliation_v2.toml`; pass `repository.reconciliation_source(policy.policy_hash)` to `calculate_report(policy, ..., as_of=as_of)` so no second connection is opened. Pass `COUNTERFACTUAL_DASHBOARD_LIMIT + 1` to `counterfactual_runs`, retain the first 20, and set `has_older_runs` from the extra header.

  Catch only known report-policy/value errors at each section boundary and map them to `DashboardSection("unavailable", "This section is unavailable.")`. Let `ReadOnlyDatabaseError`, `sqlite3.Error`, template/output failures, and invalid clock values reach the CLI's generic command error boundary. Never include `base_url`, report result objects, decision payloads, exception strings, or private data in a dashboard model.

- [ ] **Step 4: Run snapshot verification**

  Run: `pytest tests/test_dashboard.py -v`  
  Expected: PASS.

- [ ] **Step 5: Run static type and architecture checks for the new boundary**

  Run: `ruff check glassbox/dashboard.py tests/test_dashboard.py && mypy glassbox`  
  Expected: both commands succeed.

- [ ] **Step 6: Commit and push the read-only snapshot seam**

  ```bash
  git add glassbox/dashboard.py tests/test_dashboard.py
  git commit -m "feat: assemble static dashboard snapshot"
  git push origin main
  ```

### Task 3: Standalone Renderer and Atomic Publication

**Files:**
- Modify: `glassbox/dashboard.py`
- Create: `glassbox/web/templates/dashboard.html`
- Modify: `glassbox/web/static/glassbox.css`
- Modify: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `DashboardView` from Task 2 and existing `glassbox.css`.
- Produces: `render_dashboard(view: DashboardView) -> str` and `write_dashboard(path: Path, html: str, *, overwrite: bool) -> None`; raises `DashboardError` for unsafe output requests.

- [ ] **Step 1: Write failing render and publication tests**

  Add a view containing `<script>UNTRUSTED</script>` in an agent/model label and a secret-like `https://private.example/token` in a deliberately injected excluded source field. Assert:

  ```python
  html = render_dashboard(view)
  assert "<style>" in html
  assert "&lt;script&gt;UNTRUSTED&lt;/script&gt;" in html
  assert "<script" not in html
  assert "<form" not in html
  assert "href=" not in html
  assert "PRIVATE_COUNTERFACTUAL_RATIONALE" not in html
  assert "private.example" not in html
  assert "Not monitored (disabled by policy)" in html
  ```

  For the writer, create `target` containing `old`; assert `write_dashboard(target, html, overwrite=False)` raises `DashboardError` and leaves `old` intact. Assert an overwrite produces the new complete document. Monkeypatch `os.replace` to raise and assert the old target remains byte-for-byte unchanged and no `*.tmp` artifact remains in its directory.

- [ ] **Step 2: Run renderer tests to verify they fail**

  Run: `pytest tests/test_dashboard.py -k 'render or writer' -v`  
  Expected: FAIL because renderer/writer/template do not exist.

- [ ] **Step 3: Implement rendering and safe writer**

  Configure a private Jinja `Environment` with `FileSystemLoader(Path(__file__).parent / "web/templates")` and `select_autoescape(("html",))`. Render:

  ```python
  return _TEMPLATES.get_template("dashboard.html").render(
      dashboard=view,
      css=(Path(__file__).parent / "web/static/glassbox.css").read_text(encoding="utf-8"),
  )
  ```

  Implement `write_dashboard` using `tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False)`, `chmod(0o600)`, `write`, `flush`, `os.fsync`, and `os.replace`. If the target exists without `overwrite`, raise before creating the temporary file. In a `finally`, unlink a leftover temporary name only; never unlink the target.

  Template requirements: include a format-version header and one section card per typed section; use only `dashboard` fields and conditional state branches; include no URL/form/script tags. Add responsive, dashboard-only classes to the existing CSS without changing Decision Card selectors.

- [ ] **Step 4: Run renderer and output verification**

  Run: `pytest tests/test_dashboard.py -k 'render or writer or standalone' -v`  
  Expected: PASS.

- [ ] **Step 5: Commit and push static rendering**

  ```bash
  git add glassbox/dashboard.py glassbox/web/templates/dashboard.html \
    glassbox/web/static/glassbox.css tests/test_dashboard.py
  git commit -m "feat: render static operations dashboard"
  git push origin main
  ```

### Task 4: CLI Boundary, Documentation, and Dependency Enforcement

**Files:**
- Modify: `glassbox/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `pyproject.toml`
- Modify: `tests/test_architecture.py`
- Modify: `README.md`
- Modify: `TODO.md`

**Interfaces:**
- Consumes: `build_dashboard`, `render_dashboard`, `write_dashboard`, `DashboardError` from `glassbox.dashboard`.
- Produces: `glassbox dashboard --output PATH [--overwrite]`; exit `0` on success and `2` with exactly `glassbox: unable to generate dashboard` on every operational error.

- [ ] **Step 1: Write failing CLI and boundary tests**

  Add CLI tests that seed a valid database, run:

  ```python
  assert main(["--database", str(path), "dashboard", "--output", str(output)]) == 0
  assert output.is_file()
  assert "Static operations dashboard" in output.read_text(encoding="utf-8")
  ```

  Add a missing-database test asserting exit `2`, empty stdout, the exact generic stderr line, and no output file. Add an existing-output-without-overwrite test asserting the original bytes remain. Add an import test that monkeypatches `builtins.__import__` to reject `glassbox.dashboard`, then runs `trace` and `export`; both must retain their existing behavior, proving dashboard imports only in its command branch.

  Extend architecture expectations with a `dashboard-dependencies` contract whose source is `glassbox.dashboard` and whose forbidden modules are `glassbox.web.server`, `glassbox.web.auth`, `glassbox.sdk`, `glassbox.collector`, `glassbox.ops`, `glassbox.eval.counterfactual_provider`, `glassbox.eval.counterfactual_config`, `glassbox.eval.discord`, `glassbox.eval.discord_config`, and `glassbox.eval._discord_transport`. Update the expected contract count from seven to eight.

- [ ] **Step 2: Run the CLI/boundary tests to verify they fail**

  Run: `pytest tests/test_cli.py -k dashboard -v && pytest tests/test_architecture.py -k dashboard -v`  
  Expected: FAIL because the command and import contract do not exist.

- [ ] **Step 3: Add the lazy CLI branch and import contract**

  Add parser configuration before `parse_args()`:

  ```python
  dashboard_command = commands.add_parser(
      "dashboard", help="write a static operations dashboard HTML file"
  )
  dashboard_command.add_argument("--output", required=True)
  dashboard_command.add_argument("--overwrite", action="store_true")
  ```

  Add a branch before `judge` that imports only inside that branch:

  ```python
  if arguments.command == "dashboard":
      from glassbox.dashboard import DashboardError, build_dashboard, render_dashboard, write_dashboard
      try:
          view = build_dashboard(Path(arguments.database), clock=lambda: datetime.now(UTC))
          write_dashboard(Path(arguments.output), render_dashboard(view), overwrite=arguments.overwrite)
      except (DashboardError, ReadOnlyDatabaseError, OSError, sqlite3.Error, ValueError):
          print("glassbox: unable to generate dashboard", file=sys.stderr)
          return 2
      return 0
  ```

  Add the TOML contract and exact `tests/test_architecture.py` expectation. Do not add any dashboard configuration environment variables.

- [ ] **Step 4: Document the operator contract and close only the shipped TODO**

  Add a README section with the exact command, required existing database, `--overwrite` behavior, offline/read-only/single-file properties, three report sections, normal empty states, and exclusions (live server, Decision Cards/traces, prompts/blobs, scheduler/Discord health, CI). Mark only `Add dashboard generation` complete in `TODO.md`; leave CI workflow, trace latency enablement, and P2.4 usability unchecked.

- [ ] **Step 5: Run focused command and architecture checks**

  Run: `pytest tests/test_cli.py -k dashboard -v && pytest tests/test_architecture.py -k dashboard -v && lint-imports`  
  Expected: all pass and the eighth import contract is kept.

- [ ] **Step 6: Run complete verification**

  Run: `pytest -q && ruff check . && mypy glassbox && lint-imports && git diff --check`  
  Expected: all pass.

- [ ] **Step 7: Commit and push the command boundary and docs**

  ```bash
  git add glassbox/cli.py tests/test_cli.py pyproject.toml tests/test_architecture.py README.md TODO.md
  git commit -m "feat: add static operations dashboard command"
  git push origin main
  ```

## Plan Self-Review

- **Spec coverage:** Tasks 1–4 cover the bounded header-only counterfactual source, one strict read-only snapshot and `as_of`, drift/reconciliation/counterfactual sections, empty/unavailable/disabled states, autoescaped inline standalone HTML, atomic overwrite-safe output, generic CLI errors, lazy import, documentation, and isolation. Scheduler and Discord state are explicitly excluded.
- **Placeholder scan:** The plan uses concrete paths, interfaces, commands, expected outcomes, test assertions, and commit messages. Checkbox markers are task tracking, not unfilled requirements.
- **Type consistency:** Task 1 defines `Repository.counterfactual_runs`; Task 2 consumes it and defines `DashboardView`, `build_dashboard`; Task 3 adds `render_dashboard`/`write_dashboard`; Task 4 consumes those exact names in the CLI branch.
