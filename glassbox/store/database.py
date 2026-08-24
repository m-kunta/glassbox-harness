"""SQLite connection and schema initialization for the local event store."""

from __future__ import annotations

import sqlite3
from pathlib import Path


class Database:
    """An initialized SQLite database with required connection pragmas."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

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
        migration = Path(__file__).with_name("migrations") / "001_initial.sql"
        connection.executescript(migration.read_text(encoding="utf-8"))
        connection.commit()
        return cls(connection)

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self.connection.close()
