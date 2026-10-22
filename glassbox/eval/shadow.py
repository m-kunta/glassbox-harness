"""Explicit, fail-open live counterfactual submission for application code."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Condition, Lock, Thread
from types import MappingProxyType

from glassbox.store import Database, Repository
from glassbox.store.database import ReadOnlyDatabaseError
from glassbox.store.repository import CounterfactualCandidate

from .counterfactual import (
    PromptArtifact,
    load_outcome_policy,
    load_prompt_artifact,
    run_counterfactual,
)
from .counterfactual_config import CounterfactualConfig, load_counterfactual_config
from .counterfactual_provider import CounterfactualProvider
from .reconciliation import ReconciliationPolicy

_DIAGNOSTICS = (
    "disabled", "configuration_unavailable", "egress_not_authorized", "queue_full",
    "lifetime_cap", "source_decision_unavailable", "provider_timeout", "provider_failure",
    "parse_failure", "storage_failure", "completed",
)


@dataclass(frozen=True)
class ShadowArtifact:
    prompt: PromptArtifact
    outcome_policy: ReconciliationPolicy


def load_shadow_artifact(prompt_path: Path, outcome_policy_path: Path) -> ShadowArtifact:
    """Validate approved artifacts during setup; invalid inputs raise to the owner."""
    return ShadowArtifact(
        load_prompt_artifact(prompt_path), load_outcome_policy(outcome_policy_path),
    )


@dataclass(frozen=True)
class _Job:
    executor: LiveShadowExecutor
    decision_id: str
    artifact: ShadowArtifact
    config: CounterfactualConfig


class _Dispatcher:
    """One queue and lifetime budget for all explicitly constructed executors."""

    def __init__(self) -> None:
        self.condition = Condition()
        self.queue: Queue[_Job] = Queue(maxsize=1)
        self.accepted = 0
        self.worker: Thread | None = None

    def admit(self, job: _Job) -> None:
        with self.condition:
            if job.executor._closed:
                job.executor._increment("disabled")
                return
            if self.accepted >= 10:
                job.executor._increment("lifetime_cap")
                return
            try:
                self.queue.put_nowait(job)
            except Full:
                job.executor._increment("queue_full")
                return
            self.accepted += 1
            job.executor._pending += 1
            if self.worker is None:
                try:
                    worker = Thread(target=self._work, name="glassbox-live-shadow", daemon=True)
                    worker.start()
                    self.worker = worker
                except Exception:
                    # The lock prevents the worker from consuming a partially admitted job.
                    self.queue.get_nowait()
                    self.queue.task_done()
                    self.accepted -= 1
                    job.executor._pending -= 1
                    raise

    def _work(self) -> None:
        while True:
            with self.condition:
                try:
                    job = self.queue.get_nowait()
                except Empty:
                    self.worker = None
                    return
            try:
                job.executor._execute(job)
            finally:
                with self.condition:
                    job.executor._pending -= 1
                    self.queue.task_done()
                    self.condition.notify_all()
                # Do not retain a completed application's configuration or artifacts.
                del job


_dispatcher = _Dispatcher()


class LiveShadowExecutor:
    """An application-owned handle to bounded process-wide background execution.

    ``submit`` only resolves configuration and attempts admission. ``shutdown``
    closes this handle and waits for its accepted work, without cancelling jobs
    belonging to any handle. A timeout leaves accepted work running as a daemon.
    The ten-job budget is never replenished by completion or shutdown.
    """

    def __init__(
        self, database_path: Path, project_root: Path, blob_dir: Path | None, *,
        environ: Mapping[str, str], clock: Callable[[], datetime],
        monotonic_clock: Callable[[], float], sleep: Callable[[float], None],
        provider_factory: Callable[[CounterfactualConfig], CounterfactualProvider],
    ) -> None:
        self._database_path = database_path
        self._project_root = project_root
        self._blob_dir = blob_dir
        self._environ = environ
        self._clock = clock
        self._monotonic_clock = monotonic_clock
        self._sleep = sleep
        self._provider_factory = provider_factory
        self._counts = dict.fromkeys(_DIAGNOSTICS, 0)
        self._counts_lock = Lock()
        self._dispatcher = _dispatcher
        # Both fields below are protected by the dispatcher's condition lock.
        self._closed = False
        self._pending = 0

    @property
    def diagnostics(self) -> Mapping[str, int]:
        """Return a read-only, consistent snapshot of this handle's counters."""
        with self._counts_lock:
            return MappingProxyType(self._counts.copy())

    def _increment(self, key: str) -> None:
        with self._counts_lock:
            self._counts[key] += 1

    def submit(self, decision_id: str, artifact: ShadowArtifact) -> None:
        """Fail open without waiting for source storage or provider work."""
        try:
            with self._dispatcher.condition:
                closed = self._closed
            if closed or self._environ.get("GLASSBOX_SHADOW_ENABLED") != "1":
                self._increment("disabled")
                return
            config = load_counterfactual_config(self._project_root, self._environ)
            if config.is_remote and self._environ.get("GLASSBOX_SHADOW_ALLOW_EGRESS") != "1":
                self._increment("egress_not_authorized")
                return
            self._dispatcher.admit(_Job(self, decision_id, artifact, config))
        except Exception:
            self._increment("configuration_unavailable")

    def shutdown(self, timeout: float | None = None) -> None:
        """Close admission and wait only for this handle's accepted jobs."""
        with self._dispatcher.condition:
            self._closed = True
            self._dispatcher.condition.wait_for(lambda: self._pending == 0, timeout=timeout)

    def _wait_for_source(self, job: _Job) -> CounterfactualCandidate | None:
        deadline = self._monotonic_clock() + 5.0
        while self._monotonic_clock() < deadline:
            database: Database | None = None
            candidate = None
            try:
                # Do not let SQLite's default busy wait extend the polling budget.
                database = Database.open_read_only(self._database_path, busy_timeout_ms=0)
                candidate = Repository(database).counterfactual_candidate(
                    job.decision_id, job.artifact.outcome_policy.policy_hash,
                )
            except ReadOnlyDatabaseError:
                # A collector may not have created its database yet. Other read
                # failures are operational failures, not absent decisions.
                if self._database_path.exists():
                    raise
            finally:
                if database is not None:
                    database.close()
            remaining = deadline - self._monotonic_clock()
            if remaining <= 0:
                break
            if candidate is not None:
                return candidate
            self._sleep(min(0.05, remaining))
        return None

    def _execute(self, job: _Job) -> None:
        try:
            candidate = self._wait_for_source(job)
            if candidate is None:
                self._increment("source_decision_unavailable")
                return
            report = run_counterfactual(
                self._database_path, job.config, job.artifact.prompt, job.artifact.outcome_policy,
                candidate.decision.event.decided_at, 1, self._blob_dir,
                clock=self._clock, provider_factory=self._provider_factory,
                preselected_candidates=(candidate,), execution_mode="live_shadow",
                enrich_outcomes=False, request_timeout_seconds=10.0,
            )
            result, = report.results
            if result.failure_code is None:
                self._increment("completed")
            elif result.failure_code == "provider_timeout":
                self._increment("provider_timeout")
            elif result.failure_code in {"provider_failure", "provider_unavailable"}:
                self._increment("provider_failure")
            else:
                # Includes invalid provider JSON and invalid captured input.
                self._increment("parse_failure")
        except Exception:
            self._increment("storage_failure")
