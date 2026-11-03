"""Short-lived scheduler wrapper tests with injected child processes."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from glassbox.ops.scheduler_runner import main, rotate_log

ERROR = "glassbox: scheduled drift run failed\n"
SECRET = "runner-secret-fixture"


def _args(tmp_path: Path) -> list[str]:
    return [
        "--database", str(tmp_path / "db.sqlite3"), "--agent", "agent ; $(echo secret)",
        "--policy", str(tmp_path / "policy.toml"),
        "--stdout-log", str(tmp_path / "logs" / "drift-monitor.stdout.log"),
        "--stderr-log", str(tmp_path / "logs" / "drift-monitor.stderr.log"),
    ]


def _child(code: int = 0) -> SimpleNamespace:
    return SimpleNamespace(wait=lambda: code)


def test_runner_rotates_logs_then_invokes_fixed_drift_argv(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    for name in ("stdout", "stderr"):
        path = logs / f"drift-monitor.{name}.log"
        path.write_bytes(b"x" * (1 << 20))
        path.with_suffix(".log.1").write_bytes(b"old")
    calls: list[list[str]] = []
    waits: list[bool] = []
    handles = []

    def spawn(argv, **kwargs):
        calls.append(argv)
        assert kwargs.keys() == {"stdout", "stderr", "shell"}
        assert kwargs["shell"] is False
        for name in ("stdout", "stderr"):
            path = logs / f"drift-monitor.{name}.log"
            assert path.with_suffix(".log.1").read_bytes() == b"x" * (1 << 20)
            assert path.stat().st_mode & 0o777 == 0o600
            handle = kwargs[name]
            assert Path(handle.name) == path
            handle.write(name.encode())
            handle.flush()
            handles.append(handle)
        return SimpleNamespace(wait=lambda: waits.append(True) or 7)

    assert main(_args(tmp_path), spawn=spawn) == 7
    assert calls == [[sys.executable, "-m", "glassbox.cli", "--database",
                      str(tmp_path / "db.sqlite3"), "drift", "--agent",
                      "agent ; $(echo secret)", "--policy", str(tmp_path / "policy.toml")]]
    assert waits == [True]
    assert all(handle.closed for handle in handles)
    assert (logs / "drift-monitor.stdout.log").read_bytes() == b"stdout"
    assert (logs / "drift-monitor.stderr.log").read_bytes() == b"stderr"


def test_runner_creates_private_log_directory_and_files(tmp_path: Path) -> None:
    assert main(_args(tmp_path), spawn=lambda *a, **kw: _child()) == 0
    logs = tmp_path / "logs"
    assert logs.stat().st_mode & 0o777 == 0o700
    assert {path.name for path in logs.iterdir()} == {
        "drift-monitor.stdout.log", "drift-monitor.stderr.log",
    }
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in logs.iterdir())


def test_runner_appends_small_logs_and_makes_existing_files_private(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    stdout = logs / "drift-monitor.stdout.log"
    stdout.write_bytes(b"previous\n")
    stdout.chmod(0o644)

    def spawn(argv, **kwargs):
        kwargs["stdout"].write(b"next\n")
        return _child()

    assert main(_args(tmp_path), spawn=spawn) == 0
    assert stdout.read_bytes() == b"previous\nnext\n"
    assert stdout.stat().st_mode & 0o777 == 0o600
    assert not stdout.with_suffix(".log.1").exists()


@pytest.mark.parametrize("size,rotated", [(0, False), ((1 << 20) - 1, False),
                                          (1 << 20, True), ((1 << 20) + 1, True)])
def test_rotate_log_has_exact_size_threshold(tmp_path: Path, size: int, rotated: bool) -> None:
    path = tmp_path / "drift-monitor.stdout.log"
    path.write_bytes(b"x" * size)
    rotate_log(path)
    assert path.exists() is not rotated
    assert path.with_suffix(".log.1").exists() is rotated


def test_rotate_missing_log_is_noop(tmp_path: Path) -> None:
    rotate_log(tmp_path / "drift-monitor.stdout.log")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("unsafe", ["relative", "name", "different-parent", "symlink",
                                    "directory", "fifo", "backup-symlink", "backup-directory",
                                    "parent-symlink", "hardlink"])
def test_runner_rejects_unsafe_log_paths_before_spawning(
    tmp_path: Path, capsys, unsafe: str,
) -> None:
    args = _args(tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    stdout = logs / "drift-monitor.stdout.log"
    target = tmp_path / "target"
    target.write_text(SECRET)
    backup = stdout.with_suffix(".log.1")
    if unsafe == "relative":
        args[7] = "relative/drift-monitor.stdout.log"
    elif unsafe == "name":
        args[7] = str(logs / "other.log")
    elif unsafe == "different-parent":
        args[9] = str(tmp_path / "other" / "drift-monitor.stderr.log")
    elif unsafe == "symlink":
        stdout.symlink_to(target)
    elif unsafe == "directory":
        stdout.mkdir()
    elif unsafe == "fifo":
        os.mkfifo(stdout)
    elif unsafe == "backup-symlink":
        backup.symlink_to(target)
    elif unsafe == "backup-directory":
        backup.mkdir()
    elif unsafe == "parent-symlink":
        logs.rmdir()
        logs.symlink_to(tmp_path, target_is_directory=True)
    else:
        os.link(target, stdout)
    calls = []
    assert main(args, spawn=lambda *a, **kw: calls.append(a) or _child()) == 2
    assert calls == []
    assert capsys.readouterr() == ("", ERROR)
    assert target.read_text() == SECRET


@pytest.mark.parametrize("args", [[], ["--help"], ["--unknown", SECRET]])
def test_invalid_arguments_have_only_fixed_error(args: list[str], capsys) -> None:
    assert main(args, spawn=lambda *a, **kw: pytest.fail("spawned")) == 2
    assert capsys.readouterr() == ("", ERROR)


@pytest.mark.parametrize("field,value", [(1, "relative"), (3, " "), (3, "bad\x00agent"),
                                         (5, "relative"), (7, "bad\x00log")])
def test_invalid_argument_values_fail_safely(
    tmp_path: Path, capsys, field: int, value: str,
) -> None:
    args = _args(tmp_path)
    args[field] = value
    assert main(args, spawn=lambda *a, **kw: pytest.fail("spawned")) == 2
    assert capsys.readouterr() == ("", ERROR)
    assert not (tmp_path / "logs").exists()


@pytest.mark.parametrize("stage", ["mkdir", "rotate", "open", "spawn", "wait"])
def test_filesystem_and_child_failures_have_only_fixed_error(
    tmp_path: Path, monkeypatch, capsys, stage: str,
) -> None:
    from glassbox.ops import scheduler_runner

    def fail(*args, **kwargs):
        raise OSError(SECRET)

    spawn = lambda *a, **kw: _child()  # noqa: E731
    if stage == "mkdir":
        monkeypatch.setattr(Path, "mkdir", fail)
    elif stage == "rotate":
        monkeypatch.setattr(scheduler_runner, "rotate_log", fail)
    elif stage == "open":
        monkeypatch.setattr(os, "open", fail)
    elif stage == "spawn":
        spawn = fail
    else:
        spawn = lambda *a, **kw: SimpleNamespace(wait=fail)  # noqa: E731
    assert main(_args(tmp_path), spawn=spawn) == 2
    assert capsys.readouterr() == ("", ERROR)
