from __future__ import annotations

import multiprocessing
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from glassbox.events import TraceEvent
from glassbox.store import Database
from glassbox.store.database import ReadOnlyDatabaseError
from glassbox.store.repository import Repository

STORE_ROOT = Path(__file__).parents[2] / "glassbox" / "store"
FIRST_TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
SECOND_TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
TIMESTAMP = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def _create_pre_strict_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            (STORE_ROOT / "migrations" / "000_pre_strict_initial.sql").read_text(
                encoding="utf-8"
            )
        )
        connection.commit()
    finally:
        connection.close()


def _create_released_strict_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            (STORE_ROOT / "migrations" / "001_initial.sql").read_text(encoding="utf-8")
        )
        connection.commit()
    finally:
        connection.close()


def test_open_read_only_requires_an_existing_database(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite3"

    with pytest.raises(ReadOnlyDatabaseError, match="does not exist"):
        Database.open_read_only(missing)

    assert missing.exists() is False


def test_open_read_only_accepts_the_current_strict_schema(tmp_path: Path) -> None:
    path = tmp_path / "glassbox.sqlite3"
    writable = Database.open(path)
    writable.close()

    readonly = Database.open_read_only(path)
    try:
        assert readonly.connection.execute("SELECT count(*) FROM decisions").fetchone()[0] == 0
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            readonly.connection.execute("UPDATE traces SET status = 'error'")
    finally:
        readonly.close()


def test_open_upgrades_the_released_strict_schema_with_feedback(tmp_path: Path) -> None:
    path = tmp_path / "released.sqlite3"
    _create_released_strict_database(path)

    writable = Database.open(path)
    try:
        assert writable.connection.execute("SELECT count(*) FROM feedback").fetchone()[0] == 0
    finally:
        writable.close()

    readonly = Database.open_read_only(path)
    readonly.close()


def test_open_read_only_rejects_pre_strict_schema(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    _create_pre_strict_database(path)

    with pytest.raises(ReadOnlyDatabaseError, match="writer-capable"):
        Database.open_read_only(path)


def test_open_read_only_rejects_an_unsupported_schema(tmp_path: Path) -> None:
    path = tmp_path / "unsupported.sqlite3"
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE traces (note TEXT)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ReadOnlyDatabaseError, match="unsupported schema"):
        Database.open_read_only(path)


def test_open_read_only_normalizes_non_sqlite_input(tmp_path: Path) -> None:
    path = tmp_path / "not-a-database.sqlite3"
    path.write_bytes(b"this is not a sqlite database")

    with pytest.raises(ReadOnlyDatabaseError, match="unsupported schema"):
        Database.open_read_only(path)


def test_open_read_only_rejects_a_negative_busy_timeout(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="busy_timeout_ms must be non-negative"):
        Database.open_read_only(tmp_path / "anything.sqlite3", busy_timeout_ms=-1)


def _trace(trace_id: str, started_at: datetime) -> TraceEvent:
    return TraceEvent(
        trace_id=trace_id,
        agent_name="wal-test-agent",
        agent_version="test",
        started_at=started_at,
        environment="dev",
    )


def _live_wal_writer(
    path: str,
    first_written: multiprocessing.synchronize.Event,
    write_second: multiprocessing.synchronize.Event,
    second_written: multiprocessing.synchronize.Event,
    release_writer: multiprocessing.synchronize.Event,
) -> None:
    database = Database.open(path)
    try:
        repository = Repository(database)
        repository.write_event(_trace(FIRST_TRACE_ID, TIMESTAMP))
        first_written.set()
        if not write_second.wait(timeout=10):
            raise TimeoutError("parent did not request second WAL write")
        repository.write_event(_trace(SECOND_TRACE_ID, TIMESTAMP + timedelta(seconds=1)))
        second_written.set()
        if not release_writer.wait(timeout=10):
            raise TimeoutError("parent did not finish WAL read")
    finally:
        database.close()


def _trace_exists(path: Path, trace_id: str) -> bool:
    database = Database.open_read_only(path)
    try:
        return Repository(database).trace_tree(trace_id) is not None
    finally:
        database.close()


def test_read_only_connection_sees_live_wal_commits_from_another_process(tmp_path: Path) -> None:
    path = tmp_path / "live.sqlite3"
    context = multiprocessing.get_context("spawn")
    first_written = context.Event()
    write_second = context.Event()
    second_written = context.Event()
    release_writer = context.Event()
    process = context.Process(
        target=_live_wal_writer,
        args=(
            str(path),
            first_written,
            write_second,
            second_written,
            release_writer,
        ),
    )
    process.start()

    try:
        assert first_written.wait(timeout=10)
        assert _trace_exists(path, FIRST_TRACE_ID)

        write_second.set()
        assert second_written.wait(timeout=10)

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if _trace_exists(path, SECOND_TRACE_ID):
                break
            time.sleep(0.05)
        else:
            pytest.fail("read-only connection did not observe the live second WAL commit")
    finally:
        release_writer.set()
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=10)

    assert process.exitcode == 0
