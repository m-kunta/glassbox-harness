"""Read-only command-line inspection of persisted Glassbox traces."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from glassbox.eval.runner import run_suite
from glassbox.store import Database, Repository, TraceTree


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Glassbox inspection command and return its process status."""
    parser = argparse.ArgumentParser(prog="glassbox")
    parser.add_argument(
        "--database",
        default=os.environ.get("GLASSBOX_DATABASE", "glassbox.sqlite3"),
        help="existing Glassbox SQLite database (default: GLASSBOX_DATABASE or glassbox.sqlite3)",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    trace_command = commands.add_parser("trace", help="print one persisted trace tree as JSON")
    trace_command.add_argument("trace_id")
    eval_command = commands.add_parser("eval", help="run one deterministic evaluation suite")
    eval_command.add_argument("--suite", required=True)
    arguments = parser.parse_args(argv)

    if arguments.command == "eval":
        try:
            evaluation = run_suite(Path(arguments.suite))
        except (OSError, ValueError) as exc:
            print(f"glassbox: unable to evaluate suite: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(evaluation, sort_keys=True, separators=(",", ":")))
        return 0 if evaluation["gates"]["passed"] else 1

    database_path = Path(arguments.database)
    try:
        trace_tree = _read_trace_tree(database_path, arguments.trace_id)
    except FileNotFoundError:
        print(f"glassbox: database does not exist: {database_path}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print(f"glassbox: unable to read database: {exc}", file=sys.stderr)
        return 2

    if trace_tree is None:
        print(f"glassbox: trace not found: {arguments.trace_id}", file=sys.stderr)
        return 1

    print(json.dumps(_trace_tree_payload(trace_tree), sort_keys=True, separators=(",", ":")))
    return 0


def _read_trace_tree(database_path: Path, trace_id: str) -> TraceTree | None:
    """Read a trace through the existing repository without mutating its SQLite file."""
    if not database_path.is_file():
        raise FileNotFoundError(database_path)
    # immutable=1 would make SQLite skip the WAL file entirely, hiding any
    # trace committed but not yet checkpointed by a still-running writer.
    connection = sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return Repository(Database(connection)).trace_tree(trace_id)
    finally:
        connection.close()


def _trace_tree_payload(trace_tree: TraceTree) -> dict[str, Any]:
    """Convert typed repository records into the public, deterministic JSON shape."""
    return {
        "trace": trace_tree.trace.model_dump(mode="json", exclude_none=True),
        "spans": [span.model_dump(mode="json", exclude_none=True) for span in trace_tree.spans],
        "decisions": [
            {
                "decision": stored.event.model_dump(mode="json", exclude_none=True),
                "evidence": [
                    evidence.model_dump(mode="json", exclude_none=True)
                    for evidence in stored.evidence
                ],
            }
            for stored in trace_tree.decisions
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
