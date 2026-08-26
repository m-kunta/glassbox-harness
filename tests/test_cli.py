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


def test_trace_command_does_not_create_sqlite_sidecars(tmp_path: Path, capsys) -> None:
    from glassbox.cli import main

    database_path = tmp_path / "glassbox.sqlite3"
    database = _repository_with_trace(database_path)
    database.close()
    files_before = {path.name for path in tmp_path.iterdir()}

    assert main(["--database", str(database_path), "trace", TRACE_ID]) == 0
    capsys.readouterr()

    assert {path.name for path in tmp_path.iterdir()} == files_before
