# macOS Drift Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in macOS LaunchAgent that runs the existing drift command daily at 09:00 local time with safe lifecycle controls.

**Architecture:** `glassbox.ops.scheduler` owns validation, plist rendering, ownership checks, atomic lifecycle operations, and injected `launchctl`. The short-lived `glassbox.ops.scheduler_runner` rotates logs and starts ordinary drift argv. `glassbox.cli` is the only public boundary.

**Tech Stack:** Python 3.11+, `plistlib`, `subprocess`, `pathlib`, `tempfile`, macOS `launchctl`, pytest, Ruff, mypy, import-linter.

## Global Constraints

- Support only macOS user LaunchAgents. Other platforms return exit `2` without writes or `launchctl` calls.
- Use label `com.glassbox.drift-monitor`, managed path `~/Library/LaunchAgents/com.glassbox.drift-monitor.plist`, and `StartCalendarInterval={Hour=9, Minute=0}`. Never set `RunAtLoad` or `KeepAlive`.
- Require absolute existing regular database/policy files and successful `glassbox.eval.drift.load_policy()` before mutation.
- Require a Glassbox ownership marker and exact label before replace, unload, or removal; refuse foreign/malformed plists.
- Write new plist mode `0600` via same-directory temporary file, fsync, atomic replacement, then bootstrap. On bootstrap failure remove only the new owned plist.
- Never serialize/display tokens, webhook URLs, `.env` contents, exception text, or child output.
- Runner logs are exactly `~/Library/Logs/Glassbox/drift-monitor.stdout.log` and `drift-monitor.stderr.log`; rotate at 1 MiB, retaining one `.1` file.
- Invoke ordinary `glassbox drift --database … --agent … --policy …`; no daemon, retries, changed drift semantics, or alternate alert path.
- Scheduler can use stdlib and `glassbox.eval.drift` policy parsing. SDK, collector, web, and eval cannot import scheduler modules; scheduler cannot import Discord/provider modules.
- Exclude Linux/Windows, GitHub Actions, CI gating, custom cadence, dashboard state, escalation, and background scheduling loops.

---

## File structure

| File | Responsibility |
| --- | --- |
| `glassbox/ops/__init__.py` | Operations package marker. |
| `glassbox/ops/scheduler.py` | Settings, plist, ownership, atomic lifecycle, injected launchctl. |
| `glassbox/ops/scheduler_runner.py` | Log rotation and short-lived drift child. |
| `glassbox/cli.py` | Scheduler parser and safe CLI errors. |
| `pyproject.toml` | Import-linter scheduler rules. |
| `tests/ops/test_scheduler.py` | Lifecycle, plist, ownership, and platform tests. |
| `tests/ops/test_scheduler_runner.py` | Rotation, argv, and child-exit tests. |
| `tests/test_cli.py`, `tests/test_architecture.py` | Public boundary and docs/import contracts. |
| `README.md`, `TODO.md` | Operator instructions and backlog state. |

### Task 1: Implement the safe LaunchAgent boundary

**Files:**

- Create: `glassbox/ops/__init__.py`, `glassbox/ops/scheduler.py`, `tests/ops/test_scheduler.py`
- Modify: `pyproject.toml`, `tests/test_architecture.py`

**Interfaces:**

```python
SCHEDULER_LABEL = "com.glassbox.drift-monitor"
OWNERSHIP_MARKER = "Glassbox managed drift monitor"

@dataclass(frozen=True)
class DriftScheduleSettings:
    database_path: Path; agent_name: str; policy_path: Path
    executable: Path; plist_path: Path; logs_directory: Path

@dataclass(frozen=True)
class SchedulerStatus:
    state: Literal["not_installed", "installed_not_loaded", "loaded", "unavailable"]
    hour: int | None; minute: int | None
    stdout_log: Path | None; stderr_log: Path | None

class SchedulerError(ValueError): ...
def install_drift_schedule(settings: DriftScheduleSettings, *, replace: bool, platform: str, uid: int, run_launchctl: Callable[[Sequence[str]], None]) -> None: ...
def drift_schedule_status(settings: DriftScheduleSettings, *, platform: str, uid: int, run_launchctl: Callable[[Sequence[str]], str]) -> SchedulerStatus: ...
def remove_drift_schedule(settings: DriftScheduleSettings, *, platform: str, uid: int, run_launchctl: Callable[[Sequence[str]], None]) -> None: ...
```

- [ ] **Step 1: Write failing lifecycle tests.**

Create temporary valid database/policy/executable files and injected fake launchctl.

```python
def test_install_writes_owned_daily_launchagent(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    settings = _settings(tmp_path)
    install_drift_schedule(settings, replace=False, platform="darwin", uid=501,
                           run_launchctl=lambda argv: calls.append(tuple(argv)))
    plist = plistlib.loads(settings.plist_path.read_bytes())
    assert plist["Label"] == "com.glassbox.drift-monitor"
    assert plist["StartCalendarInterval"] == {"Hour": 9, "Minute": 0}
    assert "RunAtLoad" not in plist and "KeepAlive" not in plist
    assert settings.plist_path.stat().st_mode & 0o777 == 0o600
    assert calls == [("bootstrap", "gui/501", str(settings.plist_path))]
```

Also cover foreign/malformed plist refusal, policy validation before writes/calls,
non-macOS no-op error, replace gate, unload-before-replace, bootstrap rollback,
status states, removal, and secret fixture absence.

- [ ] **Step 2: Prove tests fail.**

Run: `.venv/bin/pytest tests/ops/test_scheduler.py -v`

Expected: FAIL because `glassbox.ops.scheduler` does not exist.

- [ ] **Step 3: Implement lifecycle and plist.**

Validate settings; render XML through `plistlib.dumps(..., fmt=plistlib.FMT_XML,
sort_keys=True)` with this exact program core:

```python
{"Label": SCHEDULER_LABEL,
 "ProgramArguments": [str(settings.executable), "-m", "glassbox.ops.scheduler_runner",
   "--database", str(settings.database_path), "--agent", settings.agent_name,
   "--policy", str(settings.policy_path), "--stdout-log", str(settings.logs_directory / "drift-monitor.stdout.log"),
   "--stderr-log", str(settings.logs_directory / "drift-monitor.stderr.log")],
 "ProcessType": "Background", "StartCalendarInterval": {"Hour": 9, "Minute": 0}}
```

Use `os.open(..., 0o600)`, file/directory fsync, and `Path.replace`; require marker
plus label before mutation. Call exact `bootout gui/<uid> <plist>`, `bootstrap
gui/<uid> <plist>`, and `print gui/<uid>/com.glassbox.drift-monitor` vectors. Use
fixed safe error messages.

- [ ] **Step 4: Add import restrictions and verify.**

Append scheduler and runner modules to sdk/collector/eval/web forbidden targets.
Add `ops-scheduler-dependencies` forbidding SDK, collector, web, and three Discord
modules from scheduler sources; update the architecture test's contract count from
six to seven and assert the new contract is kept.

Run: `.venv/bin/pytest tests/ops/test_scheduler.py tests/test_architecture.py -q && .venv/bin/ruff check glassbox/ops tests/ops tests/test_architecture.py && .venv/bin/mypy glassbox/ops && .venv/bin/lint-imports`

Expected: PASS.

- [ ] **Step 5: Commit Task 1.**

Run: `git add glassbox/ops tests/ops pyproject.toml tests/test_architecture.py && git commit -m "feat: add macOS drift scheduler boundary"`

### Task 2: Add the short-lived scheduled runner

**Files:**

- Create: `glassbox/ops/scheduler_runner.py`, `tests/ops/test_scheduler_runner.py`
- Modify: `glassbox/ops/scheduler.py`, `tests/ops/test_scheduler.py`

**Interfaces:** Task 1's plist executes `python -m glassbox.ops.scheduler_runner`.

- [ ] **Step 1: Write failing runner tests.**

```python
def test_runner_rotates_log_then_invokes_fixed_drift_argv(tmp_path: Path) -> None:
    stdout = tmp_path / "drift-monitor.stdout.log"
    stdout.write_bytes(b"x" * (1 << 20))
    seen: list[list[str]] = []
    result = main(["--database", str(tmp_path / "db.sqlite3"), "--agent", "agent",
                   "--policy", str(tmp_path / "policy.toml"), "--stdout-log", str(stdout),
                   "--stderr-log", str(tmp_path / "drift-monitor.stderr.log")],
                  spawn=lambda argv, **kwargs: seen.append(argv) or _child(0))
    assert result == 0
    assert stdout.with_suffix(".log.1").read_bytes() == b"x" * (1 << 20)
    assert seen[0][:6] == [sys.executable, "-m", "glassbox.cli", "--database", str(tmp_path / "db.sqlite3"), "drift"]
```

Cover `0700` log directory creation, replacement of old `.1`, unsafe log path
rejection, no shell use, stdout/stderr redirection, child status propagation, and
fixed safe error/exit `2` for spawn/filesystem failure.

- [ ] **Step 2: Prove runner tests fail.**

Run: `.venv/bin/pytest tests/ops/test_scheduler_runner.py -v`

Expected: FAIL because the runner module does not exist.

- [ ] **Step 3: Implement runner.**

```python
LOG_ROTATION_BYTES = 1 << 20
def rotate_log(path: Path) -> None: ...
def main(argv: Sequence[str] | None = None, *, spawn: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen) -> int: ...
```

Rotate active logs at 1 MiB by replacing prior `.1` then atomically moving the
active file. Create fresh logs `0600`, and call one child with this exact argv:

```python
[sys.executable, "-m", "glassbox.cli", "--database", database,
 "drift", "--agent", agent, "--policy", policy]
```

Open redirection handles, wait once, and return child status. Any argument,
filesystem, or spawn error prints only `glassbox: scheduled drift run failed`
to stderr and returns `2`.

- [ ] **Step 4: Verify and commit Task 2.**

Run: `.venv/bin/pytest tests/ops/test_scheduler.py tests/ops/test_scheduler_runner.py -q && .venv/bin/ruff check glassbox/ops tests/ops && .venv/bin/mypy glassbox/ops`

Expected: PASS.

Run: `git add glassbox/ops/scheduler_runner.py tests/ops/test_scheduler_runner.py glassbox/ops/scheduler.py tests/ops/test_scheduler.py && git commit -m "feat: add scheduled drift runner"`

### Task 3: Expose scheduler commands and document them

**Files:**

- Modify: `glassbox/cli.py`, `tests/test_cli.py`, `tests/test_architecture.py`, `README.md`, `TODO.md`

**Interfaces:** Uses Task 1 lifecycle methods through lazy import only in the
`schedule` CLI branch.

- [ ] **Step 1: Write failing CLI/docs tests.**

```python
def test_schedule_install_delegates_on_macos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("glassbox.cli.sys.platform", "darwin")
    calls: list[DriftScheduleSettings] = []
    monkeypatch.setattr("glassbox.ops.scheduler.install_drift_schedule", lambda settings, **_: calls.append(settings))
    assert main(["schedule", "install-drift", "--database", str(_db(tmp_path)),
                 "--agent", "agent", "--policy", str(_policy(tmp_path))]) == 0
    assert calls[0].agent_name == "agent"

def test_schedule_is_safe_on_non_macos(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["schedule", "status-drift"]) == 2
    assert capsys.readouterr().err == "glassbox: scheduler is only available on macOS\n"
```

Cover replacement, safe status JSON containing only state/time/log paths, generic
safe errors, and zero preflight/launchctl behavior on non-macOS. Add README
contract checks for macOS-only scope, 09:00 local schedule, all commands,
replacement/removal, logs/rotation, missed run, existing Discord role, and
deferred CI/non-macOS support.

- [ ] **Step 2: Prove tests fail.**

Run: `.venv/bin/pytest tests/test_cli.py tests/test_architecture.py -k schedule -v`

Expected: FAIL because command and guide do not exist.

- [ ] **Step 3: Implement command boundary and docs.**

Add `schedule` subcommands `install-drift`, `status-drift`, `remove-drift`.
`install-drift` has required `--database Path`, `--agent`, `--policy Path`, and
`--replace`. Lazily import scheduler; derive paths from `Path.home() / "Library"`;
use `Path(sys.executable).resolve()`, `sys.platform`, `os.getuid()`, and a
`subprocess.run(..., check=True, capture_output=True, text=True)` runner. Print
sorted compact status JSON containing state/time/log paths only. Map
`SchedulerError`, `OSError`, `subprocess.SubprocessError`, and `ValueError` to a
fixed safe error / exit `2`.

Document exact install/status/remove commands, absolute paths, local 09:00 timing,
no immediate/catch-up run, logs/rotation, ordinary drift/Discord behavior, and
explicit removal. Split the P3 scheduling/CI TODO into checked scheduler work
plus unchecked CI gating follow-up.

- [ ] **Step 4: Verify and commit Task 3.**

Run: `.venv/bin/pytest && .venv/bin/ruff check . && .venv/bin/mypy glassbox && .venv/bin/lint-imports && git diff --check`

Expected: all checks pass; existing third-party deprecation warnings may remain.

Run: `git add glassbox/cli.py tests/test_cli.py tests/test_architecture.py README.md TODO.md && git commit -m "feat: add macOS drift scheduler commands"`

## Plan self-review

- **Coverage:** Task 1 provides lifecycle/ownership/platform checks; Task 2 provides exact log and child behavior; Task 3 provides public commands and operator documentation.
- **Safety:** all tests use temporary directories and injected runners; none bootstraps a real user LaunchAgent.
- **Type consistency:** later tasks use only `DriftScheduleSettings`, `SchedulerStatus`, and lifecycle APIs defined in Task 1.
- **Scope:** no CI, GitHub Actions, other OSes, custom schedules, retries, daemon, dashboard, or escalation work is included.
