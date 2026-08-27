"""SQLite connection and schema initialization for the local event store."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock

_TABLES = (
    "traces",
    "spans",
    "decisions",
    "evidence",
    "overrides",
    "outcomes",
    "eval_runs",
    "eval_results",
)
_LEGACY_TIMESTAMP_CHECK = "strftime('%Y-%m-%dT%H:%M:%fZ'"
_LEGACY_TABLE_PREFIX = "__glassbox_pre_strict_"


class TimestampMigrationError(RuntimeError):
    """A pre-strict database cannot be upgraded without changing stored data."""


class Database:
    """An initialized SQLite database with required connection pragmas."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self._operation_lock = RLock()

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

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._operation_lock:
            self.connection.close()


def _initialize_schema(connection: sqlite3.Connection) -> None:
    schema_sql = (Path(__file__).with_name("migrations") / "001_initial.sql").read_text(
        encoding="utf-8"
    )
    table_sql = _glassbox_table_sql(connection)
    if _is_pre_strict_schema(table_sql):
        _rebuild_pre_strict_schema(connection, schema_sql)
        return
    if _contains_legacy_timestamp_checks(table_sql):
        raise TimestampMigrationError(
            "Cannot migrate this Glassbox database because it is not the complete released "
            "pre-strict schema. Restore a complete backup or export and repair the database "
            "before reopening it."
        )
    connection.executescript(schema_sql)


def _glassbox_table_sql(connection: sqlite3.Connection) -> dict[str, str]:
    placeholders = ", ".join("?" for _ in _TABLES)
    rows = connection.execute(
        f"SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name IN ({placeholders})",
        _TABLES,
    )
    return {name: sql for name, sql in rows if sql is not None}


def _is_pre_strict_schema(table_sql: dict[str, str]) -> bool:
    return set(table_sql) == set(_TABLES) and all(
        _LEGACY_TIMESTAMP_CHECK in schema for schema in table_sql.values()
    )


def _contains_legacy_timestamp_checks(table_sql: dict[str, str]) -> bool:
    return any(_LEGACY_TIMESTAMP_CHECK in schema for schema in table_sql.values())


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
        for table in _TABLES:
            connection.execute(f"ALTER TABLE {table} RENAME TO {_legacy_table_name(table)}")
        _execute_schema_statements(connection, schema_sql)
        for table in _TABLES:
            failed_table = table
            connection.execute(
                f"INSERT INTO {table} SELECT * FROM {_legacy_table_name(table)}"
            )
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise TimestampMigrationError(
                "Cannot migrate the pre-strict Glassbox database because it contains "
                "foreign-key violations. Repair the legacy database before reopening it."
            )
        for table in _TABLES:
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


def _execute_schema_statements(connection: sqlite3.Connection, schema_sql: str) -> None:
    """Execute the controlled schema script without executescript's implicit commit."""
    for statement in schema_sql.split(";"):
        if statement.strip():
            connection.execute(statement)
