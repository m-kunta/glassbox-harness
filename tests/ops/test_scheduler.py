"""Isolated LaunchAgent lifecycle tests; never invoke real launchctl."""

import os
import plistlib
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from glassbox.ops.scheduler import (
    OWNERSHIP_MARKER,
    SCHEDULER_LABEL,
    DriftScheduleSettings,
    SchedulerError,
    drift_schedule_status,
    install_drift_schedule,
    remove_drift_schedule,
)

SECRET = "scheduler-secret-fixture"


def _settings(tmp_path: Path) -> DriftScheduleSettings:
    database = tmp_path / "database.sqlite3"
    database.touch()
    policy = tmp_path / "policy.toml"
    policy.write_bytes(Path("glassbox/eval/policies/drift_v2.toml").read_bytes())
    executable = tmp_path / "python"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o700)
    return DriftScheduleSettings(
        database_path=database,
        agent_name="supply agent",
        policy_path=policy,
        executable=executable,
        plist_path=tmp_path / "LaunchAgents" / f"{SCHEDULER_LABEL}.plist",
        logs_directory=tmp_path / "Logs" / "Glassbox",
    )


def _install(settings: DriftScheduleSettings, calls: list[tuple[str, ...]]) -> None:
    install_drift_schedule(
        settings, replace=False, platform="darwin", uid=501,
        run_launchctl=lambda argv: calls.append(tuple(argv)),
    )


def _failure(argv: Sequence[str], code: int = 1) -> str:
    raise subprocess.CalledProcessError(code, argv, output=SECRET, stderr=SECRET)


def test_install_writes_owned_daily_launchagent(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    settings = _settings(tmp_path)
    monkeypatch.setenv("GLASSBOX_DISCORD_WEBHOOK_URL", SECRET)
    (tmp_path / ".env").write_text(SECRET)
    _install(settings, calls)
    data = settings.plist_path.read_bytes()
    plist = plistlib.loads(data)
    assert plist["Label"] == SCHEDULER_LABEL == "com.glassbox.drift-monitor"
    assert plist["Comment"] == OWNERSHIP_MARKER == "Glassbox managed drift monitor"
    assert plist["StartCalendarInterval"] == {"Hour": 9, "Minute": 0}
    assert plist["ProcessType"] == "Background"
    assert "RunAtLoad" not in plist and "KeepAlive" not in plist
    assert plist["ProgramArguments"] == [
        str(settings.executable), "-m", "glassbox.ops.scheduler_runner",
        "--database", str(settings.database_path), "--agent", settings.agent_name,
        "--policy", str(settings.policy_path),
        "--stdout-log", str(settings.logs_directory / "drift-monitor.stdout.log"),
        "--stderr-log", str(settings.logs_directory / "drift-monitor.stderr.log"),
    ]
    assert data.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')
    assert SECRET.encode() not in data
    assert settings.plist_path.stat().st_mode & 0o777 == 0o600
    assert calls == [("bootstrap", "gui/501", str(settings.plist_path))]
    assert list(settings.plist_path.parent.iterdir()) == [settings.plist_path]


@pytest.mark.parametrize("field", ["database_path", "policy_path", "executable"])
@pytest.mark.parametrize("invalid", ["relative", "missing", "directory"])
def test_install_rejects_invalid_files_before_writes_or_calls(
    tmp_path: Path, field: str, invalid: str,
) -> None:
    settings = _settings(tmp_path)
    path = {"relative": Path("relative"), "missing": tmp_path / "missing",
            "directory": tmp_path}[invalid]
    settings = replace(settings, **{field: path})
    calls: list[tuple[str, ...]] = []
    with pytest.raises(SchedulerError):
        _install(settings, calls)
    assert not settings.plist_path.parent.exists()
    assert not settings.logs_directory.exists()
    assert calls == []


@pytest.mark.parametrize("field", ["plist_path", "logs_directory"])
def test_install_requires_absolute_output_paths(tmp_path: Path, field: str) -> None:
    settings = replace(_settings(tmp_path), **{field: Path("relative")})
    calls: list[tuple[str, ...]] = []
    with pytest.raises(SchedulerError):
        _install(settings, calls)
    assert calls == []


@pytest.mark.parametrize("agent", ["", "  ", "bad\x00agent"])
def test_install_rejects_invalid_agent_without_mutation(tmp_path: Path, agent: str) -> None:
    settings = replace(_settings(tmp_path), agent_name=agent)
    calls: list[tuple[str, ...]] = []
    with pytest.raises(SchedulerError):
        _install(settings, calls)
    assert calls == []
    assert not settings.plist_path.parent.exists()


def test_policy_parser_runs_before_replacement_mutates_existing_job(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    calls: list[tuple[str, ...]] = []
    _install(settings, calls)
    original = settings.plist_path.read_bytes()
    calls.clear()
    settings.policy_path.write_text(f'policy_version = "{SECRET}"\n')
    with pytest.raises(SchedulerError) as error:
        install_drift_schedule(settings, replace=True, platform="darwin", uid=501,
                               run_launchctl=lambda argv: calls.append(tuple(argv)))
    assert SECRET not in str(error.value)
    assert calls == []
    assert settings.plist_path.read_bytes() == original


@pytest.mark.parametrize("operation", ["install", "status", "remove"])
def test_non_macos_refuses_before_any_writes_or_calls(tmp_path: Path, operation: str) -> None:
    settings = _settings(tmp_path)
    calls: list[tuple[str, ...]] = []
    runner = lambda argv: calls.append(tuple(argv))  # noqa: E731
    with pytest.raises(SchedulerError, match="^scheduler is only available on macOS$"):
        if operation == "install":
            install_drift_schedule(settings, replace=False, platform="linux", uid=501,
                                   run_launchctl=runner)
        elif operation == "status":
            drift_schedule_status(settings, platform="linux", uid=501, run_launchctl=runner)
        else:
            remove_drift_schedule(settings, platform="linux", uid=501, run_launchctl=runner)
    assert calls == []
    assert not settings.plist_path.parent.exists()
    assert not settings.logs_directory.exists()


@pytest.mark.parametrize("content", [
    b"not a plist", plistlib.dumps([]),
    plistlib.dumps({"Label": SCHEDULER_LABEL}),
    plistlib.dumps({"Comment": OWNERSHIP_MARKER, "Label": "foreign"}),
    plistlib.dumps({"Comment": OWNERSHIP_MARKER + " extra", "Label": SCHEDULER_LABEL}),
])
@pytest.mark.parametrize("operation", ["replace", "status", "remove"])
def test_foreign_or_malformed_plist_is_never_mutated_or_unloaded(
    tmp_path: Path, content: bytes, operation: str,
) -> None:
    settings = _settings(tmp_path)
    settings.plist_path.parent.mkdir()
    settings.plist_path.write_bytes(content)
    calls: list[tuple[str, ...]] = []
    runner = lambda argv: calls.append(tuple(argv))  # noqa: E731
    with pytest.raises(SchedulerError):
        if operation == "replace":
            install_drift_schedule(settings, replace=True, platform="darwin", uid=501,
                                   run_launchctl=runner)
        elif operation == "status":
            drift_schedule_status(settings, platform="darwin", uid=501, run_launchctl=runner)
        else:
            remove_drift_schedule(settings, platform="darwin", uid=501, run_launchctl=runner)
    assert settings.plist_path.read_bytes() == content
    assert calls == []


def test_owned_replacement_requires_explicit_replace(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    calls: list[tuple[str, ...]] = []
    _install(settings, calls)
    original = settings.plist_path.read_bytes()
    calls.clear()
    with pytest.raises(SchedulerError):
        _install(settings, calls)
    assert calls == []
    assert settings.plist_path.read_bytes() == original


def test_replacement_unloads_old_job_before_atomic_replacement(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _install(settings, [])
    original = settings.plist_path.read_bytes()
    replacement = replace(settings, agent_name="replacement")
    calls: list[tuple[str, ...]] = []

    def runner(argv: Sequence[str]) -> None:
        calls.append(tuple(argv))
        if argv[0] == "bootout":
            assert settings.plist_path.read_bytes() == original
        else:
            plist = plistlib.loads(settings.plist_path.read_bytes())
            assert "replacement" in plist["ProgramArguments"]

    install_drift_schedule(replacement, replace=True, platform="darwin", uid=501,
                           run_launchctl=runner)
    assert calls == [("bootout", "gui/501", str(settings.plist_path)),
                     ("bootstrap", "gui/501", str(settings.plist_path))]


def test_failed_unload_preserves_existing_plist(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _install(settings, [])
    original = settings.plist_path.read_bytes()
    with pytest.raises(SchedulerError) as error:
        install_drift_schedule(settings, replace=True, platform="darwin", uid=501,
                               run_launchctl=_failure)
    assert SECRET not in str(error.value)
    assert settings.plist_path.read_bytes() == original


def test_bootstrap_failure_rolls_back_new_owned_plist(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(SchedulerError) as error:
        install_drift_schedule(settings, replace=False, platform="darwin", uid=501,
                               run_launchctl=_failure)
    assert SECRET not in str(error.value)
    assert not settings.plist_path.exists()
    assert list(settings.plist_path.parent.iterdir()) == []


def test_bootstrap_failure_never_deletes_a_foreign_replacement(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    foreign = plistlib.dumps({"Label": "foreign", "secret": SECRET})

    def runner(argv: Sequence[str]) -> None:
        settings.plist_path.write_bytes(foreign)
        _failure(argv)

    with pytest.raises(SchedulerError, match="^scheduler installation failed$"):
        install_drift_schedule(settings, replace=False, platform="darwin", uid=501,
                               run_launchctl=runner)
    assert settings.plist_path.read_bytes() == foreign


def test_status_not_installed_never_calls_launchctl(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    calls: list[tuple[str, ...]] = []
    status = drift_schedule_status(settings, platform="darwin", uid=501,
                                   run_launchctl=lambda argv: calls.append(tuple(argv)))
    assert status.state == "not_installed"
    assert (status.hour, status.minute, status.stdout_log, status.stderr_log) == (None,) * 4
    assert calls == []


@pytest.mark.parametrize("result,expected", [
    ("loaded", "loaded"), (113, "installed_not_loaded"), (1, "unavailable"),
    ("oserror", "unavailable"),
])
def test_status_reports_safe_states_and_metadata_without_live_inputs(
    tmp_path: Path, result: str | int, expected: str,
) -> None:
    settings = _settings(tmp_path)
    _install(settings, [])
    settings.database_path.unlink()
    settings.policy_path.unlink()
    calls: list[tuple[str, ...]] = []

    def runner(argv: Sequence[str]) -> str:
        calls.append(tuple(argv))
        if isinstance(result, int):
            return _failure(argv, result)
        if result == "oserror":
            raise OSError(SECRET)
        return SECRET

    status = drift_schedule_status(settings, platform="darwin", uid=501, run_launchctl=runner)
    assert status.state == expected
    assert (status.hour, status.minute) == (9, 0)
    assert status.stdout_log == settings.logs_directory / "drift-monitor.stdout.log"
    assert status.stderr_log == settings.logs_directory / "drift-monitor.stderr.log"
    assert SECRET not in repr(status)
    assert calls == [("print", "gui/501/com.glassbox.drift-monitor")]


@pytest.mark.parametrize("loaded", [True, False])
def test_remove_unloads_only_if_loaded_and_leaves_inputs_and_logs(
    tmp_path: Path, loaded: bool,
) -> None:
    settings = _settings(tmp_path)
    _install(settings, [])
    settings.logs_directory.mkdir(parents=True)
    log = settings.logs_directory / "drift-monitor.stdout.log"
    log.write_text(SECRET)
    settings.policy_path.unlink()  # Cleanup must work after prior inputs disappear.
    calls: list[tuple[str, ...]] = []

    def runner(argv: Sequence[str]) -> None:
        calls.append(tuple(argv))
        assert settings.plist_path.exists()
        if argv[0] == "print" and not loaded:
            _failure(argv, 113)

    remove_drift_schedule(settings, platform="darwin", uid=501, run_launchctl=runner)
    assert calls == [("print", "gui/501/com.glassbox.drift-monitor")] + (
        [("bootout", "gui/501", str(settings.plist_path))] if loaded else []
    )
    assert not settings.plist_path.exists()
    assert settings.database_path.exists()
    assert log.read_text() == SECRET


@pytest.mark.parametrize("stage", ["print", "bootout"])
def test_remove_failure_keeps_plist_and_hides_exception(tmp_path: Path, stage: str) -> None:
    settings = _settings(tmp_path)
    _install(settings, [])
    original = settings.plist_path.read_bytes()

    def runner(argv: Sequence[str]) -> None:
        if argv[0] == stage:
            _failure(argv)

    with pytest.raises(SchedulerError) as error:
        remove_drift_schedule(settings, platform="darwin", uid=501, run_launchctl=runner)
    assert SECRET not in str(error.value)
    assert settings.plist_path.read_bytes() == original


def test_remove_not_installed_is_idempotent(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    calls: list[tuple[str, ...]] = []
    remove_drift_schedule(settings, platform="darwin", uid=501,
                          run_launchctl=lambda argv: calls.append(tuple(argv)))
    assert calls == []


@pytest.mark.parametrize("operation", ["replace", "remove", "status"])
def test_symlink_at_managed_path_is_refused(tmp_path: Path, operation: str) -> None:
    settings = _settings(tmp_path)
    _install(settings, [])
    target = tmp_path / "original.plist"
    settings.plist_path.rename(target)
    settings.plist_path.symlink_to(target)
    original = target.read_bytes()
    calls: list[tuple[str, ...]] = []
    runner = lambda argv: calls.append(tuple(argv))  # noqa: E731
    with pytest.raises(SchedulerError):
        if operation == "replace":
            install_drift_schedule(settings, replace=True, platform="darwin", uid=501,
                                   run_launchctl=runner)
        elif operation == "remove":
            remove_drift_schedule(settings, platform="darwin", uid=501, run_launchctl=runner)
        else:
            drift_schedule_status(settings, platform="darwin", uid=501, run_launchctl=runner)
    assert calls == []
    assert settings.plist_path.is_symlink()
    assert target.read_bytes() == original


def test_install_fsyncs_file_and_directory(tmp_path: Path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    seen: list[bool] = []
    fsync = os.fsync

    def record(fd: int) -> None:
        import stat
        seen.append(stat.S_ISDIR(os.fstat(fd).st_mode))
        fsync(fd)

    monkeypatch.setattr(os, "fsync", record)
    _install(settings, [])
    assert False in seen and True in seen


def test_remove_rechecks_ownership_after_status_query(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _install(settings, [])
    foreign = plistlib.dumps({"Label": "foreign"})
    calls: list[tuple[str, ...]] = []

    def runner(argv: Sequence[str]) -> None:
        calls.append(tuple(argv))
        if argv[0] == "print":
            settings.plist_path.write_bytes(foreign)

    with pytest.raises(SchedulerError):
        remove_drift_schedule(settings, platform="darwin", uid=501, run_launchctl=runner)
    assert calls == [("print", "gui/501/com.glassbox.drift-monitor")]
    assert settings.plist_path.read_bytes() == foreign


@pytest.mark.parametrize("change", [
    {"StartCalendarInterval": {"Hour": True, "Minute": 0}},
    {"StartCalendarInterval": {"Hour": 9, "Minute": 60}},
    {"ProgramArguments": []},
    {"ProgramArguments": ["--stdout-log", 42]},
    {"ProgramArguments": ["--stdout-log", "relative/drift-monitor.stdout.log"]},
])
def test_status_rejects_malformed_metadata_before_query(tmp_path: Path, change: dict) -> None:
    settings = _settings(tmp_path)
    _install(settings, [])
    plist = plistlib.loads(settings.plist_path.read_bytes())
    settings.plist_path.write_bytes(plistlib.dumps(plist | change))
    calls: list[tuple[str, ...]] = []
    with pytest.raises(SchedulerError):
        drift_schedule_status(settings, platform="darwin", uid=501,
                              run_launchctl=lambda argv: calls.append(tuple(argv)))
    assert calls == []


def test_status_ignores_arbitrary_non_log_arguments_and_child_output(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _install(settings, [])
    plist = plistlib.loads(settings.plist_path.read_bytes())
    plist["ProgramArguments"].extend(["--credential", SECRET])
    settings.plist_path.write_bytes(plistlib.dumps(plist))
    status = drift_schedule_status(settings, platform="darwin", uid=501,
                                   run_launchctl=lambda argv: SECRET)
    assert SECRET not in repr(status)


def test_install_filesystem_failure_is_safe_and_never_calls_launchctl(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.plist_path.parent.write_text(SECRET)
    calls: list[tuple[str, ...]] = []
    with pytest.raises(SchedulerError) as error:
        _install(settings, calls)
    assert SECRET not in str(error.value)
    assert calls == []
    assert settings.plist_path.parent.read_text() == SECRET


def test_install_rejects_nonexecutable_interpreter_before_writes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.executable.chmod(0o600)
    calls: list[tuple[str, ...]] = []
    with pytest.raises(SchedulerError):
        _install(settings, calls)
    assert not settings.plist_path.parent.exists()
    assert calls == []


@pytest.mark.parametrize("replacing", [False, True])
def test_install_preserves_foreign_destination_created_during_temporary_write(
    tmp_path: Path, monkeypatch, replacing: bool,
) -> None:
    settings = _settings(tmp_path)
    if replacing:
        _install(settings, [])
    foreign = plistlib.dumps({"Label": "foreign", "secret": SECRET})
    calls: list[tuple[str, ...]] = []
    original_open = os.open

    def intercept_open(path, flags, mode=0o777):
        if Path(path).suffix == ".tmp":
            settings.plist_path.write_bytes(foreign)
        return original_open(path, flags, mode)

    monkeypatch.setattr(os, "open", intercept_open)
    with pytest.raises(SchedulerError) as error:
        install_drift_schedule(settings, replace=replacing, platform="darwin", uid=501,
                               run_launchctl=lambda argv: calls.append(tuple(argv)))
    assert SECRET not in str(error.value)
    assert settings.plist_path.read_bytes() == foreign
    assert calls == ([("bootout", "gui/501", str(settings.plist_path))] if replacing else [])
    assert list(settings.plist_path.parent.iterdir()) == [settings.plist_path]


@pytest.mark.parametrize("operation", ["install", "status", "remove"])
def test_malformed_plist_date_is_always_a_safe_scheduler_error(
    tmp_path: Path, operation: str,
) -> None:
    settings = _settings(tmp_path)
    settings.plist_path.parent.mkdir()
    malformed = (
        '<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict>'
        f'<key>Label</key><string>{SCHEDULER_LABEL}</string>'
        f'<key>Comment</key><string>{OWNERSHIP_MARKER}</string>'
        f'<key>UntrustedDate</key><date>{SECRET}</date></dict></plist>'
    ).encode()
    settings.plist_path.write_bytes(malformed)
    calls: list[tuple[str, ...]] = []
    runner = lambda argv: calls.append(tuple(argv))  # noqa: E731
    with pytest.raises(SchedulerError, match="^scheduler plist is malformed$") as error:
        if operation == "install":
            install_drift_schedule(settings, replace=True, platform="darwin", uid=501,
                                   run_launchctl=runner)
        elif operation == "status":
            drift_schedule_status(settings, platform="darwin", uid=501, run_launchctl=runner)
        else:
            remove_drift_schedule(settings, platform="darwin", uid=501, run_launchctl=runner)
    assert SECRET not in str(error.value)
    assert calls == []
    assert settings.plist_path.read_bytes() == malformed


def test_replacement_restores_foreign_file_swapped_after_final_validation(
    tmp_path: Path, monkeypatch,
) -> None:
    from glassbox.ops import scheduler

    settings = _settings(tmp_path)
    _install(settings, [])
    foreign = plistlib.dumps({"Label": "foreign", "secret": SECRET})
    original_owned = scheduler._owned
    reads = 0

    def intercept_owned(path: Path):
        nonlocal reads
        result = original_owned(path)
        if path == settings.plist_path:
            reads += 1
            if reads == 3:
                settings.plist_path.write_bytes(foreign)
        return result

    monkeypatch.setattr(scheduler, "_owned", intercept_owned)
    calls: list[tuple[str, ...]] = []
    with pytest.raises(SchedulerError) as error:
        install_drift_schedule(settings, replace=True, platform="darwin", uid=501,
                               run_launchctl=lambda argv: calls.append(tuple(argv)))
    assert reads >= 3
    assert SECRET not in str(error.value)
    assert settings.plist_path.read_bytes() == foreign
    assert calls == [("bootout", "gui/501", str(settings.plist_path))]
    assert list(settings.plist_path.parent.iterdir()) == [settings.plist_path]


def test_failed_atomic_swap_preserves_original_owned_plist(tmp_path: Path, monkeypatch) -> None:
    from glassbox.ops import scheduler

    settings = _settings(tmp_path)
    _install(settings, [])
    original = settings.plist_path.read_bytes()

    def failed_swap(first: Path, second: Path) -> None:
        raise OSError(SECRET)

    monkeypatch.setattr(scheduler, "_swap_paths", failed_swap)
    calls: list[tuple[str, ...]] = []
    with pytest.raises(SchedulerError) as error:
        install_drift_schedule(settings, replace=True, platform="darwin", uid=501,
                               run_launchctl=lambda argv: calls.append(tuple(argv)))
    assert SECRET not in str(error.value)
    assert settings.plist_path.read_bytes() == original
    assert calls == [("bootout", "gui/501", str(settings.plist_path))]
    assert list(settings.plist_path.parent.iterdir()) == [settings.plist_path]


def test_failed_rollback_preserves_displaced_foreign_bytes(tmp_path: Path, monkeypatch) -> None:
    from glassbox.ops import scheduler

    settings = _settings(tmp_path)
    _install(settings, [])
    foreign = plistlib.dumps({"Label": "foreign", "secret": SECRET})
    real_swap = scheduler._swap_paths
    swaps = 0

    def failed_rollback(first: Path, second: Path) -> None:
        nonlocal swaps
        swaps += 1
        if swaps == 1:
            settings.plist_path.write_bytes(foreign)
            real_swap(first, second)
        else:
            raise OSError(SECRET)

    monkeypatch.setattr(scheduler, "_swap_paths", failed_rollback)
    calls: list[tuple[str, ...]] = []
    with pytest.raises(SchedulerError) as error:
        install_drift_schedule(settings, replace=True, platform="darwin", uid=501,
                               run_launchctl=lambda argv: calls.append(tuple(argv)))
    assert SECRET not in str(error.value)
    assert swaps == 2
    displaced = list(settings.plist_path.parent.glob("*.tmp"))
    assert len(displaced) == 1
    assert displaced[0].read_bytes() == foreign
    assert calls == [("bootout", "gui/501", str(settings.plist_path))]
