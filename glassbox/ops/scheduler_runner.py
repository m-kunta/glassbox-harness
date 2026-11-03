"""Rotate private scheduler logs and wait for one ordinary drift invocation."""

import argparse
import os
import stat
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NoReturn

LOG_ROTATION_BYTES = 1 << 20


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ValueError("invalid scheduled drift arguments")


def _regular_or_missing(path: Path) -> os.stat_result | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError("unsafe scheduled log")
    return metadata


def rotate_log(path: Path) -> None:
    """Replace the single backup with an active log of at least 1 MiB."""
    backup = path.with_suffix(".log.1")
    metadata = _regular_or_missing(path)
    _regular_or_missing(backup)
    if metadata is not None and metadata.st_size >= LOG_ROTATION_BYTES:
        path.replace(backup)


def _private_open(path: str, flags: int) -> int:
    descriptor = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("unsafe scheduled log")
        os.fchmod(descriptor, 0o600)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = _Parser(add_help=False, allow_abbrev=False)
    for name in ("database", "agent", "policy", "stdout-log", "stderr-log"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args(argv)
    if any("\x00" in value for value in vars(args).values()):
        raise ValueError("invalid scheduled drift arguments")
    if (not args.agent.strip() or not Path(args.database).is_absolute()
            or not Path(args.policy).is_absolute()):
        raise ValueError("invalid scheduled drift arguments")
    stdout, stderr = Path(args.stdout_log), Path(args.stderr_log)
    if (not stdout.is_absolute() or not stderr.is_absolute()
            or stdout.name != "drift-monitor.stdout.log"
            or stderr.name != "drift-monitor.stderr.log" or stdout.parent != stderr.parent):
        raise ValueError("unsafe scheduled logs")
    for directory in stdout.parents:
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise ValueError("unsafe scheduled log directory")
    for path in (stdout, stderr):
        _regular_or_missing(path)
        _regular_or_missing(path.with_suffix(".log.1"))
    return args


def main(
    argv: Sequence[str] | None = None, *,
    spawn: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
) -> int:
    """Run one drift child, returning its status or a safe operational error."""
    try:
        args = _arguments(argv)
        stdout, stderr = Path(args.stdout_log), Path(args.stderr_log)
        stdout.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        rotate_log(stdout)
        rotate_log(stderr)
        with open(stdout, "ab", opener=_private_open) as out:
            with open(stderr, "ab", opener=_private_open) as err:
                child = spawn(
                    [sys.executable, "-m", "glassbox.cli", "--database", args.database,
                     "drift", "--agent", args.agent, "--policy", args.policy],
                    stdout=out, stderr=err, shell=False,
                )
                return child.wait()
    except (OSError, ValueError, subprocess.SubprocessError):
        print("glassbox: scheduled drift run failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
