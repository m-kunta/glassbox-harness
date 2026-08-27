from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from glassbox.store import Database

STORE_ROOT = Path(__file__).parents[2] / "glassbox" / "store"

TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
SPAN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
CHILD_SPAN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FB2"
DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAX"
OVERRIDE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAY"
SUPERSEDING_OVERRIDE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FB3"
OUTCOME_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAZ"
EVAL_RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FB0"
EVAL_RESULT_ID = "01ARZ3NDEKTSV4RRFFQ69G5FB1"
TIMESTAMP = "2026-08-22T14:30:45.123Z"
INVALID_TIMESTAMP = "2026-02-30T14:30:45Z"


def _insert_trace(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        INSERT INTO traces (trace_id, agent_name, agent_version, started_at, status, environment)
        VALUES (?, 'agent', 'version', ?, 'ok', 'dev')
        """,
        (TRACE_ID, TIMESTAMP),
    )


def _insert_decision(connection: sqlite3.Connection) -> None:
    _insert_trace(connection)
    connection.execute(
        """
        INSERT INTO decisions (
            decision_id, trace_id, agent_name, agent_version, entity_type, entity_id,
            decision_type, recommendation, rationale, rationale_citations, confidence,
            alternatives_considered, decided_at
        ) VALUES (?, ?, 'agent', 'version', 'sku_dc', 'sku-1', 'flag_exception',
                  '{}', 'reason', '[]', 0.5, '[]', ?)
        """,
        (DECISION_ID, TRACE_ID, TIMESTAMP),
    )


def _insert_row(
    connection: sqlite3.Connection, table: str, values: dict[str, object]
) -> None:
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    connection.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(values.values())
    )


def _valid_row(table: str) -> dict[str, object]:
    rows = {
        "traces": {
            "trace_id": TRACE_ID,
            "agent_name": "agent",
            "agent_version": "version",
            "started_at": TIMESTAMP,
            "status": "ok",
            "environment": "dev",
        },
        "spans": {
            "span_id": SPAN_ID,
            "trace_id": TRACE_ID,
            "name": "retrieve",
            "span_kind": "retrieval",
            "started_at": TIMESTAMP,
            "attributes": "{}",
        },
        "decisions": {
            "decision_id": DECISION_ID,
            "trace_id": TRACE_ID,
            "agent_name": "agent",
            "agent_version": "version",
            "entity_type": "sku_dc",
            "entity_id": "sku-1",
            "decision_type": "flag_exception",
            "recommendation": "{}",
            "rationale": "reason",
            "rationale_citations": "[]",
            "confidence": 0.5,
            "alternatives_considered": "[]",
            "decided_at": TIMESTAMP,
        },
        "evidence": {
            "decision_id": DECISION_ID,
            "evidence_id": "inventory_position",
            "source_system": "BY",
            "source_ref": "item/1",
            "field_name": "on_hand",
            "field_value_json": "0",
            "weight": 0.8,
            "retrieved_at": TIMESTAMP,
        },
        "overrides": {
            "override_id": OVERRIDE_ID,
            "decision_id": DECISION_ID,
            "actor": "hashed-actor",
            "action": "accepted",
            "created_at": TIMESTAMP,
            "idempotency_key": "request-1",
        },
        "outcomes": {
            "outcome_id": OUTCOME_ID,
            "decision_id": DECISION_ID,
            "outcome_type": "stockout_occurred",
            "observed_at": TIMESTAMP,
            "horizon_days": 14,
            "value": '{"occurred":true}',
        },
        "eval_runs": {
            "eval_run_id": EVAL_RUN_ID,
            "suite_id": "supply-exceptions",
            "suite_version": "v1",
            "agent_version": "version",
            "run_at": TIMESTAMP,
        },
        "eval_results": {
            "eval_result_id": EVAL_RESULT_ID,
            "eval_run_id": EVAL_RUN_ID,
            "case_id": "case-1",
            "assertion_name": "is-valid",
            "passed": 1,
            "run_at": TIMESTAMP,
        },
    }
    return dict(rows[table])


def _insert_required_parents(connection: sqlite3.Connection, table: str) -> None:
    if table in {"spans", "decisions", "evidence", "overrides", "outcomes"}:
        _insert_trace(connection)
    if table in {"evidence", "overrides", "outcomes"}:
        _insert_row(connection, "decisions", _valid_row("decisions"))
    if table == "eval_results":
        _insert_row(connection, "eval_runs", _valid_row("eval_runs"))


def _legacy_timestamp_check(column: str) -> str:
    return (
        f"{column} GLOB '????-??-??T??:??:??*Z' AND "
        f"strftime('%Y-%m-%dT%H:%M:%fZ', {column}) IS NOT NULL"
    )


_LEGACY_TIMESTAMP_CHECKS = {
    "started_at": _legacy_timestamp_check("started_at"),
    "ended_at": f"ended_at IS NULL OR ({_legacy_timestamp_check('ended_at')})",
    "decided_at": _legacy_timestamp_check("decided_at"),
    "retrieved_at": _legacy_timestamp_check("retrieved_at"),
    "created_at": _legacy_timestamp_check("created_at"),
    "observed_at": _legacy_timestamp_check("observed_at"),
    "run_at": _legacy_timestamp_check("run_at"),
}


def _legacy_schema_sql() -> str:
    """Return the pre-strict version of the released initial schema."""
    strict_schema = (STORE_ROOT / "migrations" / "001_initial.sql").read_text(
        encoding="utf-8"
    )
    legacy_lines: list[str] = []
    for line in strict_schema.splitlines():
        for column, check in _LEGACY_TIMESTAMP_CHECKS.items():
            prefix = f"    {column} TEXT"
            if line.startswith(prefix):
                suffix = "," if line.endswith(",") else ""
                line = f"{line[:line.index(' CHECK ')]} CHECK ({check}){suffix}"
                break
        legacy_lines.append(line)
    return "\n".join(legacy_lines)


def _create_pre_strict_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(_legacy_schema_sql())
    return connection


def _seed_all_legacy_records(connection: sqlite3.Connection) -> None:
    _insert_trace(connection)
    _insert_row(connection, "spans", _valid_row("spans"))
    child_span = _valid_row("spans")
    child_span["span_id"] = CHILD_SPAN_ID
    child_span["parent_span_id"] = SPAN_ID
    _insert_row(connection, "spans", child_span)
    _insert_row(connection, "decisions", _valid_row("decisions"))
    _insert_row(connection, "evidence", _valid_row("evidence"))
    _insert_row(connection, "overrides", _valid_row("overrides"))
    _insert_row(connection, "outcomes", _valid_row("outcomes"))
    _insert_row(connection, "eval_runs", _valid_row("eval_runs"))
    _insert_row(connection, "eval_results", _valid_row("eval_results"))


def test_open_reports_invalid_timestamps_in_a_pre_strict_database(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    legacy = _create_pre_strict_database(path)
    invalid_trace = _valid_row("traces")
    invalid_trace["started_at"] = INVALID_TIMESTAMP
    _insert_row(legacy, "traces", invalid_trace)
    legacy.commit()
    legacy.close()

    with pytest.raises(RuntimeError, match="strict UTC RFC3339"):
        Database.open(path)

    unchanged = sqlite3.connect(path)
    try:
        assert unchanged.execute("SELECT started_at FROM traces").fetchone()[0] == INVALID_TIMESTAMP
        schema = unchanged.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'traces'"
        ).fetchone()[0]
        assert "strftime('%Y-%m-%dT%H:%M:%fZ'" in schema
    finally:
        unchanged.close()


def test_open_refuses_an_incomplete_pre_strict_schema(tmp_path: Path) -> None:
    path = tmp_path / "partial-legacy.sqlite3"
    legacy = _create_pre_strict_database(path)
    legacy.execute("DROP TABLE eval_results")
    legacy.executescript((STORE_ROOT / "migrations" / "001_initial.sql").read_text())
    legacy.commit()
    legacy.close()

    with pytest.raises(RuntimeError, match="complete released pre-strict schema"):
        Database.open(path)


def test_open_rebuilds_pre_strict_database_without_losing_records(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    legacy = _create_pre_strict_database(path)
    _seed_all_legacy_records(legacy)
    legacy.commit()
    legacy.close()

    migrated = Database.open(path)
    connection = migrated.connection

    for table, count in {
        "traces": 1,
        "spans": 2,
        "decisions": 1,
        "evidence": 1,
        "overrides": 1,
        "outcomes": 1,
        "eval_runs": 1,
        "eval_results": 1,
    }.items():
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == count
    parent_span_id = connection.execute(
        "SELECT parent_span_id FROM spans WHERE span_id = ?", (CHILD_SPAN_ID,)
    ).fetchone()[0]
    assert parent_span_id == SPAN_ID
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

    invalid_trace = _valid_row("traces")
    invalid_trace["trace_id"] = "01ARZ3NDEKTSV4RRFFQ69G5FC0"
    invalid_trace["started_at"] = INVALID_TIMESTAMP
    with pytest.raises(sqlite3.IntegrityError):
        _insert_row(connection, "traces", invalid_trace)
    migrated.close()

    reopened = Database.open(path)
    try:
        assert reopened.connection.execute("SELECT COUNT(*) FROM spans").fetchone()[0] == 2
        assert reopened.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert reopened.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5_000
    finally:
        reopened.close()


def test_open_creates_every_p0_table_and_required_indexes(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    connection = database.connection

    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    expected_tables = {
        "traces",
        "spans",
        "decisions",
        "evidence",
        "overrides",
        "outcomes",
        "eval_runs",
        "eval_results",
    }
    assert expected_tables <= tables

    indexes = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    assert {
        "idx_decisions_agent_decided_at",
        "idx_decisions_entity",
        "idx_decisions_type_confidence",
    } <= indexes


def test_schema_sql_matches_the_initial_migration() -> None:
    """schema.sql is documentation, not what Database.open() runs (see
    glassbox/store/database.py) — nothing regenerates it, so a later migration
    that isn't mirrored here would drift silently. Fail loudly instead."""
    schema_sql = (STORE_ROOT / "schema.sql").read_text(encoding="utf-8")
    initial_migration = (STORE_ROOT / "migrations" / "001_initial.sql").read_text(
        encoding="utf-8"
    )
    assert schema_sql == initial_migration


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-08-22 14:30:45Z",
        "2026-08-22T14:30:45 Z",
        "2026-02-30T14:30:45Z",
        "2026-08-22T24:00:00Z",
        "2026-08-22T14:30:45.1Z",
        "2026-08-22T14:30:45.000Z",
        "2026-08-22T14:30:45.1234Z",
        "2026-08-22T14:30:45.123000Z",
    ],
)
def test_schema_rejects_noncanonical_or_invalid_utc_timestamps(
    tmp_path: Path, timestamp: str
) -> None:
    connection = Database.open(tmp_path / "glassbox.sqlite3").connection
    values = _valid_row("traces")
    values["started_at"] = timestamp

    with pytest.raises(sqlite3.IntegrityError):
        _insert_row(connection, "traces", values)


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-08-22T14:30:45Z",
        "2026-08-22T14:30:45.123Z",
        "2026-08-22T14:30:45.123456Z",
    ],
)
def test_schema_accepts_canonical_utc_timestamps(tmp_path: Path, timestamp: str) -> None:
    connection = Database.open(tmp_path / "glassbox.sqlite3").connection
    values = _valid_row("traces")
    values["started_at"] = timestamp

    _insert_row(connection, "traces", values)


@pytest.mark.parametrize(
    ("table", "column", "invalid_value"),
    [
        ("traces", "trace_id", "not-a-ulid"),
        ("traces", "started_at", INVALID_TIMESTAMP),
        ("traces", "ended_at", INVALID_TIMESTAMP),
        ("traces", "status", "unknown"),
        ("traces", "environment", "test"),
        ("traces", "total_tokens", -1),
        ("traces", "total_cost_usd", -0.01),
        ("traces", "latency_ms", -0.01),
        ("traces", "attributes", "not-json"),
        ("spans", "span_id", "not-a-ulid"),
        ("spans", "span_kind", "unknown"),
        ("spans", "started_at", INVALID_TIMESTAMP),
        ("spans", "ended_at", INVALID_TIMESTAMP),
        ("spans", "attributes", "not-json"),
        ("spans", "tokens_in", -1),
        ("spans", "tokens_out", -1),
        ("spans", "latency_ms", -0.01),
        ("decisions", "decision_id", "not-a-ulid"),
        ("decisions", "recommendation", "not-json"),
        ("decisions", "rationale_citations", "not-json"),
        ("decisions", "confidence", -0.01),
        ("decisions", "confidence", 1.01),
        ("decisions", "alternatives_considered", "not-json"),
        ("decisions", "decided_at", INVALID_TIMESTAMP),
        ("evidence", "field_value_json", "not-json"),
        ("evidence", "retrieved_at", INVALID_TIMESTAMP),
        ("overrides", "override_id", "not-a-ulid"),
        ("overrides", "action", "unknown"),
        ("overrides", "modified_value", "not-json"),
        ("overrides", "created_at", INVALID_TIMESTAMP),
        ("outcomes", "outcome_id", "not-a-ulid"),
        ("outcomes", "observed_at", INVALID_TIMESTAMP),
        ("outcomes", "horizon_days", -1),
        ("outcomes", "value", "not-json"),
        ("outcomes", "label", "unknown"),
        ("eval_runs", "eval_run_id", "not-a-ulid"),
        ("eval_runs", "run_at", INVALID_TIMESTAMP),
        ("eval_results", "eval_result_id", "not-a-ulid"),
        ("eval_results", "passed", 2),
        ("eval_results", "run_at", INVALID_TIMESTAMP),
    ],
)
def test_schema_rejects_every_declared_check_constraint(
    tmp_path: Path, table: str, column: str, invalid_value: object
) -> None:
    connection = Database.open(tmp_path / "glassbox.sqlite3").connection
    _insert_required_parents(connection, table)
    values = _valid_row(table)
    values[column] = invalid_value

    with pytest.raises(sqlite3.IntegrityError):
        _insert_row(connection, table, values)


def test_open_is_idempotent_and_configures_sqlite_per_connection(tmp_path: Path) -> None:
    path = tmp_path / "glassbox.sqlite3"
    first = Database.open(path, busy_timeout_ms=1_234)
    second = Database.open(path, busy_timeout_ms=1_234)

    for database in (first, second):
        assert database.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert database.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 1_234
        assert database.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_schema_enforces_status_confidence_and_utc_timestamp_constraints(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    connection = database.connection

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO traces (
                trace_id, agent_name, agent_version, started_at, status, environment
            )
            VALUES (?, 'agent', 'version', ?, 'unknown', 'dev')
            """,
            (TRACE_ID, TIMESTAMP),
        )

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO traces (
                trace_id, agent_name, agent_version, started_at, status, environment
            )
            VALUES (?, 'agent', 'version', '2026-08-22T14:30:45+00:00', 'ok', 'dev')
            """,
            (TRACE_ID,),
        )

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO traces (
                trace_id, agent_name, agent_version, started_at, status, environment
            )
            VALUES (?, 'agent', 'version', '2026-99-99T99:99:99Z', 'ok', 'dev')
            """,
            (TRACE_ID,),
        )

    _insert_trace(connection)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO decisions (
                decision_id, trace_id, agent_name, agent_version, entity_type, entity_id,
                decision_type, recommendation, rationale, rationale_citations, confidence,
                alternatives_considered, decided_at
            ) VALUES (?, ?, 'agent', 'version', 'sku_dc', 'sku-1', 'flag_exception',
                      '{}', 'reason', '[]', 1.01, '[]', ?)
            """,
            (DECISION_ID, TRACE_ID, TIMESTAMP),
        )


def test_schema_rejects_non_ulid_system_identifiers(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")

    with pytest.raises(sqlite3.IntegrityError):
        database.connection.execute(
            """
            INSERT INTO traces (
                trace_id, agent_name, agent_version, started_at, status, environment
            )
            VALUES ('not-a-ulid', 'agent', 'version', ?, 'ok', 'dev')
            """,
            (TIMESTAMP,),
        )


def test_schema_defines_foreign_keys_and_decision_scoped_evidence_keys(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    connection = database.connection

    expected_foreign_keys = {
        "spans": {"traces", "spans"},
        "decisions": {"traces"},
        "evidence": {"decisions"},
        "overrides": {"decisions", "overrides"},
        "outcomes": {"decisions"},
        "eval_results": {"eval_runs"},
    }
    for table, parents in expected_foreign_keys.items():
        foreign_keys = {row[2] for row in connection.execute(f"PRAGMA foreign_key_list({table})")}
        assert foreign_keys == parents

    _insert_decision(connection)
    connection.execute(
        """
        INSERT INTO evidence (
            decision_id, evidence_id, source_system, source_ref, field_name,
            field_value_json, weight, retrieved_at
        ) VALUES (?, 'inventory_position', 'BY', 'item/1', 'on_hand', '0', 0.8, ?)
        """,
        (DECISION_ID, TIMESTAMP),
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO evidence (
                decision_id, evidence_id, source_system, source_ref, field_name,
                field_value_json, weight, retrieved_at
            ) VALUES (?, 'inventory_position', 'BY', 'item/1', 'on_hand', '1', 0.8, ?)
            """,
            (DECISION_ID, TIMESTAMP),
        )


def test_unlabelled_trace_deletion_cascades_to_spans_decisions_and_evidence(tmp_path: Path) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    connection = database.connection
    _insert_decision(connection)
    connection.execute(
        """
        INSERT INTO spans (span_id, trace_id, name, span_kind, started_at, attributes)
        VALUES (?, ?, 'retrieve', 'retrieval', ?, '{}')
        """,
        (SPAN_ID, TRACE_ID, TIMESTAMP),
    )
    connection.execute(
        """
        INSERT INTO evidence (
            decision_id, evidence_id, source_system, source_ref, field_name,
            field_value_json, weight, retrieved_at
        ) VALUES (?, 'inventory_position', 'BY', 'item/1', 'on_hand', '0', 0.8, ?)
        """,
        (DECISION_ID, TIMESTAMP),
    )

    connection.execute("DELETE FROM traces WHERE trace_id = ?", (TRACE_ID,))

    for table in ("traces", "spans", "decisions", "evidence"):
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_parent_span_deletion_cascades_to_child_spans(tmp_path: Path) -> None:
    connection = Database.open(tmp_path / "glassbox.sqlite3").connection
    _insert_trace(connection)
    _insert_row(connection, "spans", _valid_row("spans"))
    child = _valid_row("spans")
    child["span_id"] = CHILD_SPAN_ID
    child["parent_span_id"] = SPAN_ID
    _insert_row(connection, "spans", child)

    connection.execute("DELETE FROM spans WHERE span_id = ?", (SPAN_ID,))

    assert connection.execute("SELECT COUNT(*) FROM spans").fetchone()[0] == 0


def test_eval_run_deletion_cascades_to_eval_results(tmp_path: Path) -> None:
    connection = Database.open(tmp_path / "glassbox.sqlite3").connection
    _insert_row(connection, "eval_runs", _valid_row("eval_runs"))
    _insert_row(connection, "eval_results", _valid_row("eval_results"))

    connection.execute("DELETE FROM eval_runs WHERE eval_run_id = ?", (EVAL_RUN_ID,))

    assert connection.execute("SELECT COUNT(*) FROM eval_results").fetchone()[0] == 0


@pytest.mark.parametrize("label_table", ["overrides", "outcomes"])
def test_overrides_and_outcomes_restrict_trace_deletion(tmp_path: Path, label_table: str) -> None:
    database = Database.open(tmp_path / "glassbox.sqlite3")
    connection = database.connection
    _insert_decision(connection)

    if label_table == "overrides":
        connection.execute(
            """
            INSERT INTO overrides (
                override_id, decision_id, actor, action, created_at, idempotency_key
            ) VALUES (?, ?, 'hashed-actor', 'accepted', ?, 'request-1')
            """,
            (OVERRIDE_ID, DECISION_ID, TIMESTAMP),
        )
    else:
        connection.execute(
            """
            INSERT INTO outcomes (
                outcome_id, decision_id, outcome_type, observed_at, horizon_days, value
            )
            VALUES (?, ?, 'stockout_occurred', ?, 14, '{"occurred":true}')
            """,
            (OUTCOME_ID, DECISION_ID, TIMESTAMP),
        )

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM traces WHERE trace_id = ?", (TRACE_ID,))


@pytest.mark.parametrize("child_table", ["overrides", "outcomes"])
def test_overrides_and_outcomes_restrict_direct_decision_deletion(
    tmp_path: Path, child_table: str
) -> None:
    connection = Database.open(tmp_path / "glassbox.sqlite3").connection
    _insert_decision(connection)
    _insert_row(connection, child_table, _valid_row(child_table))

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM decisions WHERE decision_id = ?", (DECISION_ID,))


def test_superseded_override_restricts_deletion_of_its_predecessor(tmp_path: Path) -> None:
    connection = Database.open(tmp_path / "glassbox.sqlite3").connection
    _insert_decision(connection)
    _insert_row(connection, "overrides", _valid_row("overrides"))
    superseding = _valid_row("overrides")
    superseding["override_id"] = SUPERSEDING_OVERRIDE_ID
    superseding["supersedes_override_id"] = OVERRIDE_ID
    superseding["idempotency_key"] = "request-2"
    _insert_row(connection, "overrides", superseding)

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("DELETE FROM overrides WHERE override_id = ?", (OVERRIDE_ID,))
