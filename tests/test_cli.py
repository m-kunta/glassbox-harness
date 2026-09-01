from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from glassbox.events import DecisionEvent, EvidenceEvent, SpanEvent, TraceEvent
from glassbox.store import Database, Repository

TRACE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
SPAN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
DECISION_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAX"
TIMESTAMP = datetime(2026, 8, 22, 14, 30, 45, tzinfo=UTC)


def _repository_with_trace(path: Path) -> Database:
    database = Database.open(path)
    repository = Repository(database)
    repository.write_event(
        TraceEvent(
            trace_id=TRACE_ID,
            agent_name="replenishment-triage",
            agent_version="test",
            started_at=TIMESTAMP,
            ended_at=TIMESTAMP,
            environment="dev",
        )
    )
    repository.write_event(
        SpanEvent(
            span_id=SPAN_ID,
            trace_id=TRACE_ID,
            name="triage.run",
            span_kind="compute",
            started_at=TIMESTAMP,
            ended_at=TIMESTAMP,
        )
    )
    repository.write_event(
        DecisionEvent(
            decision_id=DECISION_ID,
            trace_id=TRACE_ID,
            agent_name="replenishment-triage",
            agent_version="test",
            entity_type="exception",
            entity_id="EXC-REDACTED",
            decision_type="triage",
            recommendation={"action": "review"},
            rationale="A redacted test decision.",
            rationale_citations=("inventory",),
            confidence=0.8,
            alternatives_considered=(),
            decided_at=TIMESTAMP,
        )
    )
    repository.write_event(
        EvidenceEvent(
            evidence_id="inventory",
            decision_id=DECISION_ID,
            source_system="sample",
            source_ref="redacted",
            field_name="on_hand",
            field_value=0,
            weight=0.8,
            retrieved_at=TIMESTAMP,
        )
    )
    return database


def test_trace_command_emits_a_stable_trace_tree_json(tmp_path: Path, capsys) -> None:
    from glassbox.cli import main

    database_path = tmp_path / "glassbox.sqlite3"
    database = _repository_with_trace(database_path)
    database.close()

    assert main(["--database", str(database_path), "trace", TRACE_ID]) == 0

    first_output = capsys.readouterr().out
    assert main(["--database", str(database_path), "trace", TRACE_ID]) == 0
    second_output = capsys.readouterr().out

    assert first_output == second_output
    assert json.loads(first_output) == {
        "decisions": [
            {
                "decision": {
                    "agent_name": "replenishment-triage",
                    "agent_version": "test",
                    "alternatives_considered": [],
                    "confidence": 0.8,
                    "decided_at": "2026-08-22T14:30:45Z",
                    "decision_id": DECISION_ID,
                    "decision_type": "triage",
                    "entity_id": "EXC-REDACTED",
                    "entity_type": "exception",
                    "rationale": "A redacted test decision.",
                    "rationale_citations": ["inventory"],
                    "recommendation": {"action": "review"},
                    "trace_id": TRACE_ID,
                },
                "evidence": [
                    {
                        "decision_id": DECISION_ID,
                        "evidence_id": "inventory",
                        "field_name": "on_hand",
                        "field_value": 0,
                        "retrieved_at": "2026-08-22T14:30:45Z",
                        "source_ref": "redacted",
                        "source_system": "sample",
                        "weight": 0.8,
                    }
                ],
            }
        ],
        "spans": [
            {
                "ended_at": "2026-08-22T14:30:45Z",
                "attributes": {},
                "name": "triage.run",
                "span_id": SPAN_ID,
                "span_kind": "compute",
                "started_at": "2026-08-22T14:30:45Z",
                "trace_id": TRACE_ID,
            }
        ],
        "trace": {
            "agent_name": "replenishment-triage",
            "agent_version": "test",
            "attributes": {},
            "ended_at": "2026-08-22T14:30:45Z",
            "environment": "dev",
            "started_at": "2026-08-22T14:30:45Z",
            "status": "ok",
            "trace_id": TRACE_ID,
        },
    }


def test_trace_command_reads_committed_data_while_the_writer_connection_is_open(
    tmp_path: Path, capsys
) -> None:
    """A trace is fully committed as soon as write_event() returns, but in WAL
    mode that commit can still be sitting only in the -wal file until the last
    connection closes or an auto-checkpoint fires. The CLI must see it anyway --
    that's the normal case of inspecting a trace from a still-running agent."""
    from glassbox.cli import main

    database_path = tmp_path / "glassbox.sqlite3"
    database = _repository_with_trace(database_path)
    try:
        assert main(["--database", str(database_path), "trace", TRACE_ID]) == 0
    finally:
        database.close()

    output = capsys.readouterr().out
    assert json.loads(output)["trace"]["trace_id"] == TRACE_ID


def test_trace_command_never_mutates_the_database(tmp_path: Path, capsys) -> None:
    """Reading a WAL-mode database inherently needs a wal-index for any reader,
    creating -wal/-shm sidecar files even for a read-only connection -- that is
    expected and is what makes reading live, uncheckpointed data possible. What
    must never happen is a mutation of the stored records."""
    import sqlite3

    from glassbox.cli import main

    database_path = tmp_path / "glassbox.sqlite3"
    database = _repository_with_trace(database_path)
    database.close()
    inspection = sqlite3.connect(database_path)
    rows_before = inspection.execute("SELECT * FROM traces").fetchall()
    inspection.close()

    assert main(["--database", str(database_path), "trace", TRACE_ID]) == 0
    capsys.readouterr()

    inspection = sqlite3.connect(database_path)
    rows_after = inspection.execute("SELECT * FROM traces").fetchall()
    inspection.close()
    assert rows_after == rows_before


def test_eval_command_prints_result_and_returns_gate_status(tmp_path: Path, capsys) -> None:
    import yaml

    from glassbox.cli import main

    (tmp_path / "schema.json").write_text('{"type":"object","required":["urgency","action"]}')
    (tmp_path / "case.yaml").write_text(
        yaml.safe_dump({"case_id": "case-001", "input": {}, "expected_labels": {"urgency": "HIGH"}})
    )
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "target": "tests.eval.runner_target:run_case",
                "schema": "schema.json",
                "cases": ["case.yaml"],
                "gates": {"deterministic_pass_rate": 1.0},
            }
        )
    )

    assert main(["eval", "--suite", str(manifest)]) == 0
    assert json.loads(capsys.readouterr().out)["gates"]["passed"] is True
