"""Read-only command-line inspection of persisted Glassbox traces."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from glassbox.eval.runner import run_suite
from glassbox.store import Database, ReadOnlyDatabaseError, Repository, TraceTree
from glassbox.web.export import ExportError, render_decision_export, write_decision_export
from glassbox.web.read_service import ReadService
from glassbox.web.server import run_server

_DURATION_PATTERN = re.compile(r"^([1-9][0-9]*)([mhd])$")


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
    export_command = commands.add_parser("export", help="write one static Decision Card HTML file")
    export_command.add_argument("--decision", required=True)
    export_command.add_argument("--output", required=True)
    export_command.add_argument("--live-base-url")
    export_command.add_argument("--overwrite", action="store_true")
    eval_command = commands.add_parser("eval", help="run one deterministic evaluation suite")
    eval_command.add_argument("--suite", required=True)
    serve_command = commands.add_parser("serve", help="run the local Glassbox web server")
    serve_command.add_argument("--host", default="127.0.0.1")
    serve_command.add_argument("--port", type=int, default=8787)
    judge_command = commands.add_parser(
        "judge", help="run the calibrated LLM reasoning-quality judge"
    )
    judge_command.add_argument("--since", required=True, type=_parse_duration)
    judge_command.add_argument("--max-cases", type=_parse_max_cases, default=None)
    judge_command.add_argument("--allow-self-judge", action="store_true")
    judge_command.add_argument("--confirm-egress", action="store_true")
    judge_command.add_argument("--require-calibrated", action="store_true")
    drift_command = commands.add_parser("drift", help="create or inspect agent drift reports")
    drift_command.add_argument("--agent")
    drift_command.add_argument("--policy", type=Path)
    drift_subcommands = drift_command.add_subparsers(dest="drift_command")
    baseline_command = drift_subcommands.add_parser(
        "baseline", help="materialize an immutable baseline"
    )
    baseline_command.add_argument("--agent", required=True)
    baseline_command.add_argument("--policy", type=Path)
    baseline_command.add_argument("--supersede-baseline", action="store_true")
    outcomes_command = commands.add_parser("outcomes", help="import or reconcile deferred outcomes")
    outcomes_subcommands = outcomes_command.add_subparsers(dest="outcomes_command", required=True)
    outcomes_import = outcomes_subcommands.add_parser(
        "import", help="import observed JSONL outcomes"
    )
    outcomes_import.add_argument("--input", type=Path, required=True)
    outcomes_import.add_argument("--rejects", type=Path, required=True)
    outcomes_import.add_argument("--policy", type=Path)
    outcomes_report = outcomes_subcommands.add_parser("report", help="print reconciliation metrics")
    outcomes_report.add_argument("--policy", type=Path)
    arguments = parser.parse_args(argv)

    if arguments.command == "outcomes":
        from glassbox.eval.reconciliation import (
            ReconciliationError,
            default_policy_path,
            import_outcomes,
            run_reconciliation_report,
        )
        from glassbox.eval.reconciliation import (
            load_policy as load_reconciliation_policy,
        )
        from glassbox.store.database import TimestampMigrationError

        try:
            reconciliation_policy = load_reconciliation_policy(
                arguments.policy or default_policy_path()
            )
            database_path = Path(arguments.database)
            if arguments.outcomes_command == "import":
                summary = import_outcomes(
                    database_path, arguments.input, arguments.rejects, reconciliation_policy,
                    clock=lambda: datetime.now(UTC),
                )
                outcomes_payload = summary.to_dict()
                exit_code = 1 if summary.rejected else 0
            else:
                outcomes_payload = run_reconciliation_report(
                    database_path, reconciliation_policy, clock=lambda: datetime.now(UTC),
                ).to_dict()
                exit_code = 0
        except (
            ReconciliationError, ReadOnlyDatabaseError, TimestampMigrationError,
            OSError, sqlite3.Error, UnicodeError, ValueError,
        ):
            print("glassbox: unable to run outcomes command", file=sys.stderr)
            return 2
        print(json.dumps(outcomes_payload, sort_keys=True, separators=(",", ":")))
        return exit_code

    if arguments.command == "drift":
        from glassbox.eval.drift import (
            DriftPolicyError,
            create_baseline,
            default_policy_path,
            load_policy,
            run_drift_report,
        )
        from glassbox.store.database import TimestampMigrationError
        from glassbox.store.repository import DriftBaselineConflictError

        if arguments.drift_command is None and not arguments.agent:
            print("glassbox: unable to run drift command", file=sys.stderr)
            return 2
        try:
            policy = load_policy(arguments.policy or default_policy_path())
            database_path = Path(arguments.database)
            if arguments.drift_command == "baseline":
                baseline = create_baseline(
                    database_path,
                    arguments.agent,
                    policy,
                    supersede=arguments.supersede_baseline,
                    clock=lambda: datetime.now(UTC),
                )
                payload: dict[str, object] = {
                    "baseline_id": baseline.baseline_id,
                    "agent_name": baseline.agent_name,
                    "policy_version": baseline.policy_version,
                    "policy_hash": baseline.policy_hash,
                    "baseline_window": {
                        "start": baseline.baseline_start.isoformat(),
                        "end": baseline.baseline_end.isoformat(),
                    },
                    "created_at": baseline.created_at.isoformat(),
                    "supersedes_baseline_id": baseline.supersedes_baseline_id,
                }
            else:
                payload = run_drift_report(
                    database_path,
                    arguments.agent,
                    policy,
                    clock=lambda: datetime.now(UTC),
                    persist=True,
                ).to_dict()
        except (
            DriftPolicyError,
            DriftBaselineConflictError,
            ReadOnlyDatabaseError,
            TimestampMigrationError,
            OSError,
            sqlite3.Error,
            ValueError,
            KeyError,
            TypeError,
        ):
            print("glassbox: unable to run drift command", file=sys.stderr)
            return 2
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 0

    if arguments.command == "eval":
        try:
            evaluation = run_suite(Path(arguments.suite))
        except (OSError, ValueError) as exc:
            print(f"glassbox: unable to evaluate suite: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(evaluation, sort_keys=True, separators=(",", ":")))
        return 0 if evaluation["gates"]["passed"] else 1

    if arguments.command == "serve":
        try:
            run_server(host=arguments.host, port=arguments.port)
        except ValueError as exc:
            print(f"glassbox: unable to start server: {exc}", file=sys.stderr)
            return 2
        return 0

    if arguments.command == "export":
        try:
            card = ReadService(Path(arguments.database)).decision_card(arguments.decision)
            if card is None:
                print("glassbox: decision not found", file=sys.stderr)
                return 1
            html = render_decision_export(card, arguments.live_base_url)
            write_decision_export(Path(arguments.output), html, overwrite=arguments.overwrite)
        except (ExportError, ReadOnlyDatabaseError, OSError):
            print("glassbox: unable to export decision", file=sys.stderr)
            return 2
        return 0

    if arguments.command == "judge":
        # Imported here, not at module scope, so `serve`/`export`/`eval` never
        # trigger loading the judge stack's optional dependency (`dotenv`).
        from glassbox.eval.judge import build_judge_cohort, run_judge
        from glassbox.eval.judge_config import JudgeConfigError, load_judge_config
        from glassbox.store.database import TimestampMigrationError

        database_path = Path(arguments.database)
        project_root = Path.cwd()
        try:
            config = load_judge_config(project_root, os.environ)
        except JudgeConfigError as exc:
            print(f"glassbox: unable to configure judge: {exc}", file=sys.stderr)
            return 2

        cohort = build_judge_cohort(config)
        # Computed once and reused for both the preflight disclosure query
        # below and the run_judge() call at the bottom of this branch, so
        # what is disclosed for consent and what is actually judged use an
        # identical cutoff -- computing it independently in each place could
        # let a decision written between the two calls appear in one but not
        # the other.
        decided_since = datetime.now(UTC) - arguments.since

        # A missing or invalid --database path must fail loudly here, before
        # any candidate is counted or run_judge() ever opens it for writing --
        # never silently fabricate an empty database (that would defeat the
        # whole point of this preflight check). The file-existence check is
        # deliberately separate from opening it (mirrors `_read_trace_tree`
        # above): once the file is confirmed to exist, `Database.open()` --
        # not `open_read_only()` -- is used for the preflight read too, so a
        # legitimate not-yet-upgraded P2 (or older) database still upgrades
        # on this, its first, `glassbox judge` invocation. `open_read_only()`
        # only accepts the database's exact current schema fingerprint and
        # has no upgrade logic, so using it here would reject every
        # legitimate un-upgraded database as "unsupported schema".
        if not database_path.is_file():
            print(
                f"glassbox: unable to read judge candidates: Glassbox database does not "
                f"exist: {database_path}",
                file=sys.stderr,
            )
            return 2

        try:
            preflight_database = Database.open(database_path)
        except (OSError, sqlite3.Error, TimestampMigrationError) as exc:
            print(f"glassbox: unable to read judge candidates: {exc}", file=sys.stderr)
            return 2

        try:
            candidates = Repository(preflight_database).judge_candidates(cohort, decided_since)
        except (OSError, sqlite3.Error) as exc:
            print(f"glassbox: unable to read judge candidates: {exc}", file=sys.stderr)
            return 2
        finally:
            preflight_database.close()

        backlog_count = sum(1 for candidate in candidates if candidate.calibration_backlog)
        recent_count = sum(1 for candidate in candidates if candidate.recent_gated)
        self_judge_unverified_count = sum(1 for candidate in candidates if not candidate.llm_models)
        deduplicated_total = len(candidates)
        send_count = (
            deduplicated_total
            if arguments.max_cases is None
            else min(deduplicated_total, arguments.max_cases)
        )
        print(
            "glassbox: judge preflight disclosure -- "
            f"provider={config.provider} model={config.model} base_url={config.base_url} "
            f"calibration_backlog_count={backlog_count} recent_gated_count={recent_count} "
            f"deduplicated_total={deduplicated_total} send_count={send_count} "
            f"self_judge_unverified_count={self_judge_unverified_count}",
            file=sys.stderr,
        )

        if config.is_remote and not arguments.confirm_egress:
            print(
                "glassbox: remote judge provider requires --confirm-egress before any "
                "decision data leaves this machine; no request was sent",
                file=sys.stderr,
            )
            return 2

        try:
            report = run_judge(
                database_path,
                config,
                decided_since,
                arguments.max_cases,
                arguments.allow_self_judge,
            )
        except Exception as exc:
            # An operational crash here (a missing optional provider-SDK
            # extra, a provider-SDK constructor error, a SQLite error during
            # persistence, ...) must never exit 1 -- that exit code is
            # reserved for an eligible *calibrated gate* failure (see the
            # `report.status == "failed"` branch below). Left uncaught,
            # Python's default exit code for an uncaught exception is also 1,
            # which a CI pipeline using --require-calibrated could misread as
            # a real quality-gate failure instead of an operational error.
            print(f"glassbox: judge run failed: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(report.to_dict(), sort_keys=True, separators=(",", ":")))
        if report.status == "failed":
            return 1
        if report.status == "uncalibrated" and arguments.require_calibrated:
            return 1
        return 0

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


def _parse_duration(text: str) -> timedelta:
    """Parse a positive whole-number duration of the form ``<N>m``, ``<N>h``, or ``<N>d``."""
    match = _DURATION_PATTERN.fullmatch(text)
    if match is None:
        raise argparse.ArgumentTypeError(
            f"invalid duration {text!r}; expected a positive whole number followed by "
            "'m', 'h', or 'd' (e.g. '30m', '12h', '7d')"
        )
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    return timedelta(days=amount)


def _parse_max_cases(text: str) -> int:
    """Parse ``--max-cases`` as a positive integer."""
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid --max-cases value: {text!r}") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("--max-cases must be a positive integer")
    return value


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
