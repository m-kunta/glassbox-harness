"""Owned, opt-in macOS LaunchAgent lifecycle with injected launchctl execution."""

import ctypes
import os
import plistlib
import stat
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from glassbox.eval.drift import DriftPolicyError, load_policy

SCHEDULER_LABEL = "com.glassbox.drift-monitor"
OWNERSHIP_MARKER = "Glassbox managed drift monitor"
_SERVICE_NOT_FOUND = 113


@dataclass(frozen=True)
class DriftScheduleSettings:
    database_path: Path
    agent_name: str
    policy_path: Path
    executable: Path
    plist_path: Path
    logs_directory: Path


@dataclass(frozen=True)
class SchedulerStatus:
    state: Literal["not_installed", "installed_not_loaded", "loaded", "unavailable"]
    hour: int | None
    minute: int | None
    stdout_log: Path | None
    stderr_log: Path | None


class SchedulerError(ValueError):
    """Safe operational error suitable for display at the CLI boundary."""


def _platform(platform: str, settings: DriftScheduleSettings) -> None:
    if platform != "darwin":
        raise SchedulerError("scheduler is only available on macOS")
    if not settings.plist_path.is_absolute():
        raise SchedulerError("scheduler requires an absolute managed plist path")


def _owned(path: Path) -> tuple[bytes, dict[str, object]] | None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(mode):
        raise SchedulerError("scheduler plist is not a managed regular file")
    data = path.read_bytes()
    try:
        value = plistlib.loads(data)
    except Exception:
        # plistlib exposes several parser exceptions, including AttributeError
        # for malformed dates; none of that untrusted input belongs in errors.
        raise SchedulerError("scheduler plist is malformed") from None
    if (not isinstance(value, dict) or value.get("Label") != SCHEDULER_LABEL
            or value.get("Comment") != OWNERSHIP_MARKER):
        raise SchedulerError("scheduler plist is not owned by Glassbox")
    return data, value


def _render(settings: DriftScheduleSettings) -> bytes:
    for path in (settings.database_path, settings.policy_path, settings.executable):
        if not path.is_absolute() or not path.is_file():
            raise SchedulerError("scheduler requires existing absolute input files")
    if not os.access(settings.executable, os.X_OK):
        raise SchedulerError("scheduler requires an executable interpreter")
    if not settings.logs_directory.is_absolute() or not settings.agent_name.strip():
        raise SchedulerError("scheduler requires an absolute log directory and nonempty agent")
    if settings.agent_name.startswith("-"):
        raise SchedulerError("scheduler agent cannot start with an option prefix")
    try:
        load_policy(settings.policy_path)
    except DriftPolicyError:
        raise SchedulerError("scheduler drift policy is invalid") from None
    try:
        return plistlib.dumps({
            "Label": SCHEDULER_LABEL,
            "Comment": OWNERSHIP_MARKER,
            "ProgramArguments": [
                str(settings.executable), "-m", "glassbox.ops.scheduler_runner",
                "--database", str(settings.database_path), "--agent", settings.agent_name,
                "--policy", str(settings.policy_path),
                "--stdout-log", str(settings.logs_directory / "drift-monitor.stdout.log"),
                "--stderr-log", str(settings.logs_directory / "drift-monitor.stderr.log"),
            ],
            "ProcessType": "Background",
            "StartCalendarInterval": {"Hour": 9, "Minute": 0},
        }, fmt=plistlib.FMT_XML, sort_keys=True)
    except (ValueError, UnicodeError):
        raise SchedulerError("scheduler settings cannot be serialized") from None


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rename_paths(first: Path, second: Path, flags: int) -> None:
    """Apply an atomic Darwin rename operation to two directory entries."""
    try:
        rename = ctypes.CDLL(None, use_errno=True).renameatx_np
    except (OSError, AttributeError):
        raise SchedulerError("scheduler atomic replacement is unavailable") from None
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                       ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    # Darwin SDK: AT_FDCWD=-2 (sys/fcntl.h).
    if rename(-2, os.fsencode(first), -2, os.fsencode(second), flags) != 0:
        raise OSError(ctypes.get_errno(), "scheduler atomic replacement failed")


def _swap_paths(first: Path, second: Path) -> None:
    """Atomically exchange two existing names using Darwin's RENAME_SWAP."""
    _rename_paths(first, second, 2)


def _move_exclusive(first: Path, second: Path) -> None:
    """Move any entry without overwriting a destination (Darwin RENAME_EXCL)."""
    _rename_paths(first, second, 4)


def _discard_owned(path: Path, expected: bytes) -> None:
    """Capture the actual entry before validation; never unlink the public name."""
    captured = path.with_name(f".{path.name}.{uuid4().hex}.quarantine")
    _move_exclusive(path, captured)
    try:
        owned = _owned(captured)
        if owned is None or owned[0] != expected:
            raise SchedulerError("scheduler plist changed during cleanup")
    except (SchedulerError, OSError):
        # Restore foreign captures only into an empty name. If another entry
        # arrived, or restoration fails, retain the capture for recovery.
        try:
            _move_exclusive(captured, path)
        finally:
            _sync_directory(path.parent)
        raise SchedulerError("scheduler plist changed during cleanup") from None
    captured.unlink()
    _sync_directory(path.parent)


def _write_atomic(
    path: Path, data: bytes, previous: tuple[bytes, dict[str, object]] | None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    cleanup_temporary = True
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if previous is None:
            # A hard link publishes the complete file atomically and refuses
            # an intervening destination, unlike an unconditional rename.
            os.link(temporary, path)
            temporary.unlink()
        else:
            if _owned(path) != previous:
                raise SchedulerError("scheduler plist changed during replacement")
            _swap_paths(temporary, path)
            # The temporary name now holds the actual displaced destination.
            # Never discard it unless verified owned or restored to its name.
            cleanup_temporary = False
            try:
                if _owned(temporary) != previous:
                    raise SchedulerError("scheduler plist changed during replacement")
            except (SchedulerError, OSError):
                _swap_paths(temporary, path)
                # Another destination may have arrived before the rollback.
                # Discard only our exact rejected new file, never that arrival.
                rejected = _owned(temporary)
                if rejected is None or rejected[0] != data:
                    raise SchedulerError("scheduler plist changed during replacement") from None
                cleanup_temporary = True
                temporary.unlink()
                _sync_directory(path.parent)
                raise SchedulerError("scheduler plist changed during replacement") from None
            cleanup_temporary = True
            temporary.unlink()
        _sync_directory(path.parent)
    finally:
        if cleanup_temporary:
            temporary.unlink(missing_ok=True)


def install_drift_schedule(
    settings: DriftScheduleSettings, *, replace: bool, platform: str, uid: int,
    run_launchctl: Callable[[Sequence[str]], None],
) -> None:
    _platform(platform, settings)
    try:
        data = _render(settings)
        previous = _owned(settings.plist_path)
        if previous is not None:
            if not replace:
                raise SchedulerError("scheduler is already installed; replacement is required")
            run_launchctl(["bootout", f"gui/{uid}", str(settings.plist_path)])
            if _owned(settings.plist_path) != previous:
                raise SchedulerError("scheduler plist changed during replacement")
        _write_atomic(settings.plist_path, data, previous)
        try:
            run_launchctl(["bootstrap", f"gui/{uid}", str(settings.plist_path)])
        except (OSError, subprocess.SubprocessError):
            # Never remove a file swapped by another process during bootstrap.
            try:
                current = _owned(settings.plist_path)
                if current is not None and current[0] == data:
                    _discard_owned(settings.plist_path, data)
            except (SchedulerError, OSError):
                pass
            raise SchedulerError("scheduler installation failed") from None
    except (OSError, subprocess.SubprocessError):
        raise SchedulerError("scheduler installation failed") from None


def _loaded(uid: int, run_launchctl: Callable[[Sequence[str]], object]) -> bool | None:
    try:
        run_launchctl(["print", f"gui/{uid}/{SCHEDULER_LABEL}"])
    except subprocess.CalledProcessError as error:
        return False if error.returncode == _SERVICE_NOT_FOUND else None
    except (OSError, subprocess.SubprocessError):
        return None
    return True


def _metadata(plist: dict[str, object], logs_directory: Path) -> tuple[int, int, Path, Path]:
    interval = plist.get("StartCalendarInterval")
    argv = plist.get("ProgramArguments")
    if not isinstance(interval, dict) or not isinstance(argv, list):
        raise SchedulerError("scheduler plist configuration is invalid")
    hour, minute = interval.get("Hour"), interval.get("Minute")
    if (type(hour) is not int or type(minute) is not int
            or not 0 <= hour < 24 or not 0 <= minute < 60):
        raise SchedulerError("scheduler plist configuration is invalid")
    logs: list[Path] = []
    for stream in ("stdout", "stderr"):
        flag = f"--{stream}-log"
        if (argv.count(flag) != 1
                or any(isinstance(arg, str) and arg.startswith(f"{flag}=") for arg in argv)):
            raise SchedulerError("scheduler plist configuration is invalid")
        try:
            value = argv[argv.index(flag) + 1]
        except (ValueError, IndexError):
            raise SchedulerError("scheduler plist configuration is invalid") from None
        if not isinstance(value, str):
            raise SchedulerError("scheduler plist configuration is invalid")
        path = logs_directory / f"drift-monitor.{stream}.log"
        if not path.is_absolute() or value != str(path):
            raise SchedulerError("scheduler plist configuration is invalid")
        logs.append(path)
    return hour, minute, logs[0], logs[1]


def drift_schedule_status(
    settings: DriftScheduleSettings, *, platform: str, uid: int,
    run_launchctl: Callable[[Sequence[str]], str],
) -> SchedulerStatus:
    _platform(platform, settings)
    try:
        owned = _owned(settings.plist_path)
        if owned is None:
            return SchedulerStatus("not_installed", None, None, None, None)
        hour, minute, stdout, stderr = _metadata(owned[1], settings.logs_directory)
        loaded = _loaded(uid, run_launchctl)
        state: Literal["loaded", "installed_not_loaded", "unavailable"] = (
            "unavailable" if loaded is None else "loaded" if loaded else "installed_not_loaded"
        )
        return SchedulerStatus(state, hour, minute, stdout, stderr)
    except OSError:
        raise SchedulerError("scheduler status is unavailable") from None


def remove_drift_schedule(
    settings: DriftScheduleSettings, *, platform: str, uid: int,
    run_launchctl: Callable[[Sequence[str]], None],
) -> None:
    _platform(platform, settings)
    try:
        previous = _owned(settings.plist_path)
        if previous is None:
            return
        loaded = _loaded(uid, run_launchctl)
        if loaded is None:
            raise SchedulerError("scheduler removal failed")
        if _owned(settings.plist_path) != previous:
            raise SchedulerError("scheduler plist changed during removal")
        if loaded:
            run_launchctl(["bootout", f"gui/{uid}", str(settings.plist_path)])
        if _owned(settings.plist_path) != previous:
            raise SchedulerError("scheduler plist changed during removal")
        _discard_owned(settings.plist_path, previous[0])
    except (OSError, subprocess.SubprocessError):
        raise SchedulerError("scheduler removal failed") from None
