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
P2_TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FBA"
P2_DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FBB"
P2_FEEDBACK_ID = "01ARZ3NDEKTSV4RRFFQ69G5FBC"
P2_EVAL_RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FBD"
P2_EVAL_RESULT_ID = "01ARZ3NDEKTSV4RRFFQ69G5FBE"
P2_TIMESTAMP = "2026-09-07T12:00:00Z"


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


def _create_released_p2_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            (STORE_ROOT / "migrations" / "001_initial.sql").read_text(encoding="utf-8")
        )
        connection.executescript(
            (STORE_ROOT / "migrations" / "002_feedback.sql").read_text(encoding="utf-8")
        )
        connection.execute(
            """
            INSERT INTO traces (
                trace_id, agent_name, agent_version, started_at, status, environment
            )
            VALUES (?, 'agent', 'version', ?, 'ok', 'dev')
            """,
            (P2_TRACE_ID, P2_TIMESTAMP),
        )
        connection.execute(
            """
            INSERT INTO decisions (
                decision_id, trace_id, agent_name, agent_version, entity_type, entity_id,
                decision_type, recommendation, rationale, rationale_citations, confidence,
                alternatives_considered, decided_at
            ) VALUES (?, ?, 'agent', 'version', 'sku_dc', 'sku-1', 'flag_exception',
                      '{}', 'reason', '[]', 0.5, '[]', ?)
            """,
            (P2_DECISION_ID, P2_TRACE_ID, P2_TIMESTAMP),
        )
        connection.execute(
            """
            INSERT INTO feedback (feedback_id, decision_id, verdict, created_at, idempotency_key)
            VALUES (?, ?, 'agree', ?, 'p2-feedback-request')
            """,
            (P2_FEEDBACK_ID, P2_DECISION_ID, P2_TIMESTAMP),
        )
        connection.execute(
            """
            INSERT INTO eval_runs (eval_run_id, suite_id, suite_version, agent_version, run_at)
            VALUES (?, 'supply-exceptions', 'v1', 'version', ?)
            """,
            (P2_EVAL_RUN_ID, P2_TIMESTAMP),
        )
        connection.execute(
            """
            INSERT INTO eval_results (
                eval_result_id, eval_run_id, case_id, assertion_name, passed, run_at
            ) VALUES (?, ?, 'case-1', 'is-valid', 1, ?)
            """,
            (P2_EVAL_RESULT_ID, P2_EVAL_RUN_ID, P2_TIMESTAMP),
        )
        connection.commit()
    finally:
        connection.close()


def test_database_open_upgrades_released_p2_schema_to_p3(tmp_path: Path) -> None:
    path = tmp_path / "p2.sqlite3"
    _create_released_p2_database(path)

    migrated = Database.open(path)
    try:
        connection = migrated.connection
        assert connection.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == 1
        feedback_row = connection.execute(
            "SELECT decision_id, verdict, reasoning_quality_score, "
            "reasoning_quality_rubric_version FROM feedback WHERE feedback_id = ?",
            (P2_FEEDBACK_ID,),
        ).fetchone()
        assert tuple(feedback_row) == (P2_DECISION_ID, "agree", None, None)

        assert connection.execute("SELECT COUNT(*) FROM eval_runs").fetchone()[0] == 1
        eval_run_row = connection.execute(
            "SELECT run_kind, judge_provider, judge_model, rubric_version, judge_temperature, "
            "self_judge_allowed, status, status_reason, judge_failure_count "
            "FROM eval_runs WHERE eval_run_id = ?",
            (P2_EVAL_RUN_ID,),
        ).fetchone()
        assert tuple(eval_run_row) == (
            "deterministic",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )

        assert connection.execute("SELECT COUNT(*) FROM eval_results").fetchone()[0] == 1
        eval_result_row = connection.execute(
            "SELECT decision_id, self_judge_bypassed FROM eval_results WHERE eval_result_id = ?",
            (P2_EVAL_RESULT_ID,),
        ).fetchone()
        assert tuple(eval_result_row) == (None, 0)

        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        migrated.close()

    readonly = Database.open_read_only(path)
    readonly.close()


def test_drift_migration_upgrades_exact_p3a_once_and_read_only_never_upgrades(
    tmp_path: Path,
) -> None:
    path = tmp_path / "p3a.sqlite3"
    database = Database.open(path)
    database.close()
    connection = sqlite3.connect(path)
    connection.execute("DROP INDEX idx_drift_runs_agent_created")
    connection.execute("DROP INDEX idx_drift_baselines_agent_policy")
    connection.execute("DROP TABLE drift_results")
    connection.execute("DROP TABLE drift_runs")
    connection.execute("DROP TABLE drift_baselines")
    connection.commit()
    connection.close()
    with pytest.raises(ReadOnlyDatabaseError):
        Database.open_read_only(path)
    untouched = sqlite3.connect(path)
    assert (
        untouched.execute("SELECT name FROM sqlite_master WHERE name = 'drift_runs'").fetchone()
        is None
    )
    untouched.close()
    upgraded = Database.open(path)
    assert (
        upgraded.connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'drift_runs'"
        ).fetchone()
        is not None
    )
    upgraded.close()
    reopened = Database.open(path)
    reopened.close()
    readonly = Database.open_read_only(path)
    readonly.close()


@pytest.mark.parametrize(
    "collision_sql",
    (
        "CREATE INDEX idx_drift_runs_agent_created ON unrelated(id)",
        "CREATE TABLE idx_drift_runs_agent_created (id TEXT)",
    ),
)
def test_drift_migration_rejects_reserved_index_name_on_foreign_table(
    tmp_path: Path, collision_sql: str
) -> None:
    path = tmp_path / "collision.sqlite3"
    database = Database.open(path)
    database.close()
    connection = sqlite3.connect(path)
    connection.execute("DROP INDEX idx_drift_runs_agent_created")
    connection.execute("DROP INDEX idx_drift_baselines_agent_policy")
    connection.execute("DROP TABLE drift_results")
    connection.execute("DROP TABLE drift_runs")
    connection.execute("DROP TABLE drift_baselines")
    connection.execute("CREATE TABLE unrelated (id TEXT)")
    connection.execute(collision_sql)
    connection.commit()
    connection.close()
    with pytest.raises(RuntimeError, match="unsupported schema"):
        Database.open(path)


def test_drift_schema_rejects_invalid_timestamp_json_and_ulid(tmp_path: Path) -> None:
    from glassbox.store.repository import DriftBaselineInsert

    path = tmp_path / "drift-checks.sqlite3"
    database = Database.open(path)
    repository = Repository(database)
    baseline = DriftBaselineInsert(
        baseline_id="01ARZ3NDEKTSV4RRFFQ69G5FE0",
        agent_name="agent",
        policy_version="v1",
        policy_hash="a" * 64,
        baseline_start=TIMESTAMP,
        baseline_end=TIMESTAMP + timedelta(days=1),
        created_at=TIMESTAMP + timedelta(days=2),
        version_counts={},
        reference={
            name: {}
            for name in ("confidence", "decision_type", "trace_latency_ms", "trace_cost_usd")
        },
    )
    repository.record_drift_baseline(baseline)
    for field, invalid in (
        ("baseline_start", "2026-02-30T14:30:45Z"),
        ("baseline_end", "2026-09-07T12:00:00+00:00"),
        ("created_at", "2026-09-07T12:00:00.000Z"),
        ("reference_json", "{bad"),
        ("baseline_id", "invalid"),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            database.connection.execute(
                f"UPDATE drift_baselines SET {field} = ? WHERE baseline_id = ?",
                (invalid, baseline.baseline_id),
            )
        database.connection.rollback()
    database.close()


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
