from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from glassbox.store import Database

TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
SPAN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAX"
OVERRIDE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAY"
OUTCOME_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAZ"
TIMESTAMP = "2026-08-22T14:30:45.123Z"


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
