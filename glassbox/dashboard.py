"""Typed, read-only operations snapshots, independent of the live web server."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from jinja2 import Environment, FileSystemLoader, TemplateError, select_autoescape

from glassbox.eval.drift import default_policy_path as default_drift_policy_path
from glassbox.eval.drift import load_policy as load_drift_policy
from glassbox.eval.drift import run_live_drift_report
from glassbox.eval.reconciliation import ReconciliationMetrics, calculate_report, load_policy
from glassbox.store import Database, Repository
from glassbox.store.repository import CounterfactualRun
from glassbox.web.read_models import DriftReportView, build_drift_report_view

COUNTERFACTUAL_DASHBOARD_LIMIT = 20
_RECONCILIATION_POLICY_PATH = (
    Path(__file__).parent / "eval" / "policies" / "reconciliation_v2.toml"
)
_UNAVAILABLE = "This section is unavailable."
_TEMPLATES = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "web/templates"),
    autoescape=select_autoescape(("html",)),
)


class DashboardError(RuntimeError):
    """A dashboard output request cannot safely be completed."""


@dataclass(frozen=True)
class DashboardSection:
    state: Literal["ready", "empty", "unavailable"]
    message: str


@dataclass(frozen=True)
class DriftSectionView(DashboardSection):
    cohorts: tuple[DriftReportView, ...] = ()


@dataclass(frozen=True)
class ReconciliationMetricsView:
    mature: int
    labelled: int
    unlabelled: int
    tp: int
    fp: int
    tn: int
    fn: int
    precision: float | None
    recall: float | None
    f1: float | None


@dataclass(frozen=True)
class ReconciliationDecisionTypeView:
    decision_type: str
    metrics: ReconciliationMetricsView


@dataclass(frozen=True)
class ReconciliationSectionView(DashboardSection):
    policy_version: str = ""
    policy_hash: str = ""
    maturity_days: int = 0
    total_decisions: int = 0
    immature_count: int = 0
    totals: ReconciliationMetricsView | None = None
    by_decision_type: tuple[ReconciliationDecisionTypeView, ...] = ()
    outcome_notice: str = "Negative outcomes must be exported and imported explicitly."


@dataclass(frozen=True)
class CounterfactualRunView:
    run_id: str
    created_at: str
    provider: str
    model: str
    prompt_version: str
    prompt_hash: str
    execution_mode: str
    selected_count: int
    succeeded_count: int
    failed_count: int
    outcome_policy_version: str
    outcome_policy_hash: str


@dataclass(frozen=True)
class CounterfactualSectionView(DashboardSection):
    runs: tuple[CounterfactualRunView, ...] = ()
    has_older_runs: bool = False


@dataclass(frozen=True)
class DashboardView:
    generated_at: str
    drift: DriftSectionView
    reconciliation: ReconciliationSectionView
    counterfactuals: CounterfactualSectionView


def _utc_text(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC")
    return value.isoformat().replace("+00:00", "Z")


def _build_drift(repository: Repository, as_of: datetime) -> DriftSectionView:
    try:
        agents = sorted(repository.drift_agent_names())
        if not agents:
            return DriftSectionView("empty", "No recorded decision or trace activity.")
        policy = load_drift_policy(default_drift_policy_path())
        cohorts = tuple(
            build_drift_report_view(run_live_drift_report(repository, agent, policy, as_of=as_of))
            for agent in agents
        )
        return DriftSectionView("ready", "", cohorts)
    except ValueError:
        # Policy, persisted-baseline integrity, and source-value errors are local.
        return DriftSectionView("unavailable", _UNAVAILABLE)


def _metrics_view(metrics: ReconciliationMetrics) -> ReconciliationMetricsView:
    denominator = 2 * metrics.tp + metrics.fp + metrics.fn
    return ReconciliationMetricsView(
        mature=metrics.mature,
        labelled=metrics.labelled,
        unlabelled=metrics.mature - metrics.labelled,
        tp=metrics.tp,
        fp=metrics.fp,
        tn=metrics.tn,
        fn=metrics.fn,
        precision=metrics.precision,
        recall=metrics.recall,
        f1=2 * metrics.tp / denominator if denominator else None,
    )


def _build_reconciliation(
    repository: Repository, database: Database, as_of: datetime,
) -> ReconciliationSectionView:
    try:
        policy = load_policy(_RECONCILIATION_POLICY_PATH)
        source = repository.reconciliation_source(policy.policy_hash)
        report = calculate_report(policy, source, as_of=as_of)
        # A policy-scoped source omits other-policy/legacy outcomes. Check their
        # existence separately on the same snapshot before claiming no imports.
        has_outcomes = bool(database.connection.execute(
            "SELECT EXISTS (SELECT 1 FROM outcomes)"
        ).fetchone()[0])
        return ReconciliationSectionView(
            state="ready" if has_outcomes else "empty",
            message="" if has_outcomes else "No outcomes have been imported.",
            policy_version=report.policy_version,
            policy_hash=report.policy_hash,
            maturity_days=report.maturity_days,
            total_decisions=len(source),
            immature_count=len(source) - report.totals.mature,
            totals=_metrics_view(report.totals),
            by_decision_type=tuple(
                ReconciliationDecisionTypeView(name, _metrics_view(metrics))
                for name, metrics in sorted(report.by_decision_type)
            ),
        )
    except ValueError:
        return ReconciliationSectionView("unavailable", _UNAVAILABLE)


def _counterfactual_view(run: CounterfactualRun) -> CounterfactualRunView:
    created_at = _utc_text(run.created_at)
    _utc_text(run.as_of)
    _utc_text(run.decided_since)
    if run.created_at < run.as_of or run.decided_since > run.as_of:
        raise ValueError("inconsistent run timestamps")
    if re.fullmatch(r"[0-7][0-9A-HJKMNP-TV-Z]{25}", run.run_id) is None:
        raise ValueError("invalid run reference")
    for value in (run.provider, run.model, run.prompt_version, run.outcome_policy_version):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("invalid run provenance")
    for value in (run.prompt_hash, run.outcome_policy_hash):
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("invalid run hash")
    if run.execution_mode not in {"offline", "live_shadow"}:
        raise ValueError("invalid run mode")
    if any(type(count) is not int or count < 0 for count in (
        run.selected_count, run.succeeded_count, run.failed_count,
    )) or run.selected_count != run.succeeded_count + run.failed_count:
        raise ValueError("invalid run counts")
    return CounterfactualRunView(
        run_id=run.run_id,
        created_at=created_at,
        provider=run.provider,
        model=run.model,
        prompt_version=run.prompt_version,
        prompt_hash=run.prompt_hash,
        execution_mode=run.execution_mode,
        selected_count=run.selected_count,
        succeeded_count=run.succeeded_count,
        failed_count=run.failed_count,
        outcome_policy_version=run.outcome_policy_version,
        outcome_policy_hash=run.outcome_policy_hash,
    )


def _build_counterfactuals(repository: Repository) -> CounterfactualSectionView:
    try:
        runs = repository.counterfactual_runs(COUNTERFACTUAL_DASHBOARD_LIMIT + 1)
        if not runs:
            return CounterfactualSectionView("empty", "No counterfactual runs have been recorded.")
        return CounterfactualSectionView(
            "ready", "",
            tuple(_counterfactual_view(run) for run in runs[:COUNTERFACTUAL_DASHBOARD_LIMIT]),
            len(runs) > COUNTERFACTUAL_DASHBOARD_LIMIT,
        )
    except ValueError:
        return CounterfactualSectionView("unavailable", _UNAVAILABLE)


def build_dashboard(path: Path, *, clock: Callable[[], datetime]) -> DashboardView:
    """Capture one instant and one SQLite read snapshot for all sections."""
    as_of = clock()
    generated_at = _utc_text(as_of)
    database = Database.open_read_only(path)
    try:
        database.connection.execute("BEGIN")
        repository = Repository(database)
        return DashboardView(
            generated_at=generated_at,
            drift=_build_drift(repository, as_of),
            reconciliation=_build_reconciliation(repository, database, as_of),
            counterfactuals=_build_counterfactuals(repository),
        )
    finally:
        database.close()


def render_dashboard(view: DashboardView) -> str:
    """Render only typed display fields as one self-contained static document."""
    try:
        return _TEMPLATES.get_template("dashboard.html").render(
            dashboard=view,
            css=(Path(__file__).parent / "web/static/glassbox.css").read_text(encoding="utf-8"),
        )
    except TemplateError:
        raise DashboardError("unable to render dashboard") from None


def write_dashboard(path: Path, html: str, *, overwrite: bool) -> None:
    """Publish a complete private UTF-8 artifact without clobbering by default."""
    if not overwrite and (path.exists() or path.is_symlink()):
        raise DashboardError("output already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary_path.chmod(0o600)
            temporary.write(html)
            temporary.flush()
            os.fsync(temporary.fileno())
        if overwrite:
            os.replace(temporary_path, path)
        else:
            # An existence check followed by replace could clobber a concurrent
            # creator. Linking publishes the complete file only if the name is free.
            try:
                os.link(temporary_path, path)
            except FileExistsError:
                raise DashboardError("output already exists") from None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
