"""SQLite connection and schema initialization for the local event store."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from threading import RLock

_BASE_TABLES = (
    "traces",
    "spans",
    "decisions",
    "evidence",
    "overrides",
    "outcomes",
    "eval_runs",
    "eval_results",
)
_TABLES = _BASE_TABLES + ("feedback",)
_INDEXES = (
    "idx_decisions_agent_decided_at",
    "idx_decisions_entity",
    "idx_decisions_type_confidence",
)
_BASE_SCHEMA_OBJECTS = _BASE_TABLES + _INDEXES
_SCHEMA_OBJECTS = _TABLES + _INDEXES
_LEGACY_TABLE_PREFIX = "__glassbox_pre_strict_"
_JUDGE_CALIBRATION_TABLES = ("feedback", "eval_runs", "eval_results")
_JUDGE_CALIBRATION_TABLE_PREFIX = "__glassbox_pre_judge_calibration_"
_UTC_OFFSET = timedelta(0)


class TimestampMigrationError(RuntimeError):
    """A pre-strict database cannot be upgraded without changing stored data."""


class ReadOnlyDatabaseError(RuntimeError):
    """A database cannot safely serve Glassbox's strict read-only contract."""


def _timestamp_sort_key(value: str) -> int:
    """Return a stable integer ordering key for one stored UTC RFC3339 timestamp.

    Stored timestamps mix second/millisecond/microsecond precision (all valid
    per the schema's CHECK constraints), so comparing the raw text sorts
    ``...:45Z`` after ``...:45.123Z`` -- ``Z`` (0x5A) is greater than ``.``
    (0x2E) in ASCII, even though the plain-seconds instant is chronologically
    earlier. Parsing to an instant and re-deriving a single integer key fixes
    this everywhere the repository orders or bounds by timestamp.
    """
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != _UTC_OFFSET:
        raise ValueError("stored timestamp is not UTC")
    return (
        parsed.toordinal() * 86_400 + parsed.hour * 3_600 + parsed.minute * 60 + parsed.second
    ) * 1_000_000 + parsed.microsecond


def _register_timestamp_sort_key(connection: sqlite3.Connection) -> None:
    """Register the shared timestamp-ordering function on *connection*.

    Registered on every read and write connection so a query never falls back
    to comparing raw RFC3339 text (see ``_timestamp_sort_key``).
    """
    connection.create_function("glassbox_timestamp_key", 1, _timestamp_sort_key, deterministic=True)


class Database:
    """An initialized SQLite database with required connection pragmas."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self._operation_lock = RLock()
        # Registered here -- not only in open()/open_read_only() -- so every
        # Database, however its connection was constructed (e.g. the CLI's own
        # live-WAL read connection), can run the repository's timestamp-ordered
        # queries.
        _register_timestamp_sort_key(connection)

    @classmethod
    def open(cls, path: Path | str, *, busy_timeout_ms: int = 5_000) -> Database:
        """Open *path*, initialize the schema, and configure this connection."""
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be non-negative")

        database_path = Path(path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        # The collector owns writes on a background thread after this factory returns.
        connection = sqlite3.connect(database_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        _initialize_schema(connection)
        connection.commit()
        return cls(connection)

    @classmethod
    def open_read_only(cls, path: Path | str, *, busy_timeout_ms: int = 5_000) -> Database:
        """Open an existing, current-schema database without modifying it."""
        if busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be non-negative")

        database_path = Path(path)
        if not database_path.is_file():
            raise ReadOnlyDatabaseError(f"Glassbox database does not exist: {database_path}")

        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                f"{database_path.resolve().as_uri()}?mode=ro",
                uri=True,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
            schema_objects = _glassbox_schema_sql(connection)
        except sqlite3.Error as error:
            if connection is not None:
                connection.close()
            raise ReadOnlyDatabaseError(
                "Glassbox database cannot be opened read-only or has an unsupported schema."
            ) from error

        assert connection is not None
        if schema_objects == _current_schema_objects():
            return cls(connection)

        connection.close()
        if _is_pre_strict_schema(schema_objects):
            raise ReadOnlyDatabaseError(
                "Glassbox database uses the pre-strict schema; run a writer-capable "
                "Glassbox session first to migrate it."
            )
        raise ReadOnlyDatabaseError("Glassbox database has an unsupported schema.")

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._operation_lock:
            self.connection.close()


def _initialize_schema(connection: sqlite3.Connection) -> None:
    schema_sql = _migration_sql("001_initial.sql")
    schema_objects = _glassbox_schema_sql(connection)
    if not schema_objects:
        connection.executescript(schema_sql)
        _execute_schema_statements(connection, _migration_sql("002_feedback.sql"))
        _apply_judge_calibration_migration(connection)
        return
    if _is_pre_strict_schema(schema_objects):
        _rebuild_pre_strict_schema(connection, schema_sql)
        _execute_schema_statements(connection, _migration_sql("002_feedback.sql"))
        _apply_judge_calibration_migration(connection)
        return
    if schema_objects == _strict_schema_objects():
        _execute_schema_statements(connection, _migration_sql("002_feedback.sql"))
        _apply_judge_calibration_migration(connection)
        return
    if schema_objects == _released_p2_schema_objects():
        _apply_judge_calibration_migration(connection)
        return
    if schema_objects == _current_schema_objects():
        return
    raise TimestampMigrationError(
        "Cannot open this existing Glassbox database because it has an unsupported schema. "
        "Only the exact released pre-strict or current strict schema can be opened. Restore "
        "a complete backup or export and repair the database before reopening it."
    )


def _glassbox_schema_sql(connection: sqlite3.Connection) -> dict[str, str]:
    """Return DDL for every sqlite_master object using a reserved Glassbox name.

    An index or trigger shares SQLite's single schema-object namespace with
    table names without necessarily being attached to one of our tables (its
    tbl_name), so it is matched by its own name too. Any such collision ends
    up with the wrong SQL text for its reserved key, which correctly falls
    through _initialize_schema to the "unsupported schema" error instead of
    letting CREATE TABLE/INDEX crash with a raw OperationalError.
    """
    table_placeholders = ", ".join("?" for _ in _TABLES)
    rows = connection.execute(
        "SELECT name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND ("
        f"(type IN ('table', 'view') AND name IN ({table_placeholders})) "
        "OR (type IN ('index', 'trigger') "
        f"AND (tbl_name IN ({table_placeholders}) OR name IN ({table_placeholders}))))",
        _TABLES + _TABLES + _TABLES,
    )
    return {name: sql for name, sql in rows if sql is not None}


def _is_pre_strict_schema(schema_objects: dict[str, str]) -> bool:
    return schema_objects == _pre_strict_schema_objects()


@lru_cache
def _pre_strict_schema_objects() -> dict[str, str]:
    """Return SQLite-normalized DDL for the complete released legacy schema."""
    return _released_schema_objects("000_pre_strict_initial.sql")


@lru_cache
def _strict_schema_objects() -> dict[str, str]:
    """Return SQLite-normalized DDL for the complete released strict schema."""
    return _released_schema_objects("001_initial.sql", _BASE_SCHEMA_OBJECTS)


@lru_cache
def _released_p2_schema_objects() -> dict[str, str]:
    """Return SQLite-normalized DDL for the complete released P2 schema.

    P2 is the strict schema plus the append-only ``feedback`` table, before the
    P3 judge-calibration columns existed.
    """
    return _strict_schema_objects() | _released_schema_objects("002_feedback.sql", ("feedback",))


@lru_cache
def _current_schema_objects() -> dict[str, str]:
    """Return the current strict schema, including P3 judge-calibration columns."""
    return _released_p2_schema_objects() | _released_schema_objects(
        "003_judge_calibration.sql", _JUDGE_CALIBRATION_TABLES
    )


def _released_schema_objects(
    filename: str, object_names: tuple[str, ...] = _BASE_SCHEMA_OBJECTS
) -> dict[str, str]:
    schema = _migration_sql(filename)
    expected: dict[str, str] = {}
    for statement in schema.split(";"):
        normalized = _normalize_schema_sql(statement)
        for name in object_names:
            if normalized.startswith(_schema_prefix(name)):
                expected[name] = normalized
                break
    if set(expected) != set(object_names):
        raise RuntimeError("The checked-in pre-strict schema fingerprint is incomplete.")
    return expected


def _migration_sql(filename: str) -> str:
    return (Path(__file__).with_name("migrations") / filename).read_text(encoding="utf-8")


def _normalize_schema_sql(schema_sql: str) -> str:
    """Apply only SQLite's known DDL normalization to controlled SQL."""
    return schema_sql.strip().replace(" IF NOT EXISTS", "", 1)


def _schema_prefix(name: str) -> str:
    object_type = "TABLE" if name in _TABLES else "INDEX"
    return f"CREATE {object_type} {name} "


def _rebuild_pre_strict_schema(connection: sqlite3.Connection, schema_sql: str) -> None:
    """Atomically rebuild the released pre-strict schema with strict checks.

    Foreign keys are disabled only during the single rebuild transaction because
    every table is renamed before its replacement exists. A failed copy rolls
    back the renames as well as the replacement tables, leaving the legacy
    database intact for repair or export.
    """
    failed_table = "unknown"
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        for table in _BASE_TABLES:
            connection.execute(f"ALTER TABLE {table} RENAME TO {_legacy_table_name(table)}")
        _execute_schema_statements(connection, schema_sql)
        for table in _BASE_TABLES:
            failed_table = table
            connection.execute(f"INSERT INTO {table} SELECT * FROM {_legacy_table_name(table)}")
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise TimestampMigrationError(
                "Cannot migrate the pre-strict Glassbox database because it contains "
                "foreign-key violations. Repair the legacy database before reopening it."
            )
        for table in _BASE_TABLES:
            connection.execute(f"DROP TABLE {_legacy_table_name(table)}")
        _execute_schema_statements(connection, schema_sql)
        connection.execute("COMMIT")
    except sqlite3.IntegrityError as error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise TimestampMigrationError(
            "Cannot migrate the pre-strict Glassbox database: records in "
            f"{failed_table!r} violate strict UTC RFC3339 timestamp storage. "
            "Legacy tables and records were left unchanged; repair or export those records "
            "before reopening it."
        ) from error
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def _legacy_table_name(table: str) -> str:
    return f"{_LEGACY_TABLE_PREFIX}{table}"


def _apply_judge_calibration_migration(connection: sqlite3.Connection) -> None:
    """Atomically rebuild feedback/eval_runs/eval_results with P3 judge columns.

    Foreign keys are disabled only during this single rebuild transaction
    because eval_results references eval_runs, and both are renamed before
    their replacements exist. A failed copy rolls back the renames as well as
    the replacement tables, leaving the prior database intact for repair.
    Safe to run against empty tables (a fresh database) or populated ones (an
    upgrade from the released P2 schema): prior eval_runs rows become
    ``run_kind = 'deterministic'`` and prior eval_results rows get
    ``decision_id = NULL`` and ``self_judge_bypassed = 0``.
    """
    migration_sql = _migration_sql("003_judge_calibration.sql")
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.execute("BEGIN IMMEDIATE")
        for table in _JUDGE_CALIBRATION_TABLES:
            connection.execute(
                f"ALTER TABLE {table} RENAME TO {_judge_calibration_legacy_table_name(table)}"
            )
        _execute_schema_statements(connection, migration_sql)
        connection.execute(
            f"""
            INSERT INTO feedback (
                feedback_id, decision_id, verdict, reason_code, free_text,
                corrected_recommendation, created_at, idempotency_key
            )
            SELECT
                feedback_id, decision_id, verdict, reason_code, free_text,
                corrected_recommendation, created_at, idempotency_key
            FROM {_judge_calibration_legacy_table_name("feedback")}
            """
        )
        connection.execute(
            f"""
            INSERT INTO eval_runs (
                eval_run_id, suite_id, suite_version, agent_version, run_at, run_kind
            )
            SELECT
                eval_run_id, suite_id, suite_version, agent_version, run_at, 'deterministic'
            FROM {_judge_calibration_legacy_table_name("eval_runs")}
            """
        )
        connection.execute(
            f"""
            INSERT INTO eval_results (
                eval_result_id, eval_run_id, case_id, assertion_name, passed, score,
                judge_rationale, run_at, decision_id, self_judge_bypassed
            )
            SELECT
                eval_result_id, eval_run_id, case_id, assertion_name, passed, score,
                judge_rationale, run_at, NULL, 0
            FROM {_judge_calibration_legacy_table_name("eval_results")}
            """
        )
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise TimestampMigrationError(
                "Cannot migrate to the P3 judge-calibration schema because feedback, "
                "eval_runs, or eval_results contain foreign-key violations. Repair the "
                "database before reopening it."
            )
        for table in _JUDGE_CALIBRATION_TABLES:
            connection.execute(f"DROP TABLE {_judge_calibration_legacy_table_name(table)}")
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")


def _judge_calibration_legacy_table_name(table: str) -> str:
    return f"{_JUDGE_CALIBRATION_TABLE_PREFIX}{table}"


def _execute_schema_statements(connection: sqlite3.Connection, schema_sql: str) -> None:
    """Execute the controlled schema script without executescript's implicit commit."""
    for statement in schema_sql.split(";"):
        if statement.strip():
            connection.execute(statement)
