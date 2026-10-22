from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from glassbox.eval import counterfactual_provider, shadow
from glassbox.eval.counterfactual_config import CounterfactualConfig
from glassbox.eval.counterfactual_provider import (
    CounterfactualProviderTimeout,
    create_counterfactual_provider,
)
from glassbox.eval.shadow import LiveShadowExecutor, ShadowArtifact, load_shadow_artifact
from glassbox.store import Database, Repository
from tests.eval.test_counterfactual import (
    AS_OF,
    DECIDED,
    DECISION_IDS,
    FakeProvider,
    _answer,
    _seed,
)

DECISION_ID = DECISION_IDS[0]
ENV = {
    "GLASSBOX_SHADOW_ENABLED": "1",
    "GLASSBOX_COUNTERFACTUAL_PROVIDER": "ollama",
    "GLASSBOX_COUNTERFACTUAL_MODEL": "variant-model",
}
COUNTERS = {
    "disabled", "configuration_unavailable", "egress_not_authorized", "queue_full",
    "lifetime_cap", "source_decision_unavailable", "provider_timeout", "provider_failure",
    "parse_failure", "storage_failure", "completed",
}


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def artifact() -> ShadowArtifact:
    return load_shadow_artifact(
        Path("glassbox/eval/prompts/counterfactual_v1.md"),
        Path("glassbox/eval/policies/reconciliation_v2.toml"),
    )


@pytest.fixture
def executors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Callable[..., Any]]:
    # Isolate process-lifetime accounting, without a public reset API.
    monkeypatch.setattr(shadow, "_dispatcher", shadow._Dispatcher())
    instances: list[LiveShadowExecutor] = []

    def create(**kwargs: Any) -> LiveShadowExecutor:
        clock = Clock()
        options = {
            "environ": dict(ENV), "clock": lambda: AS_OF,
            "monotonic_clock": clock.monotonic, "sleep": clock.advance,
            "provider_factory": lambda _: FakeProvider([_answer()]),
        } | kwargs
        instance = LiveShadowExecutor(tmp_path / "events.sqlite3", tmp_path, None, **options)
        instances.append(instance)
        return instance

    yield create
    for instance in instances:
        instance.shutdown(timeout=2)


def wait_for_counter(executor: LiveShadowExecutor, key: str, expected: int = 1) -> None:
    deadline = time.monotonic() + 2
    yielded = threading.Event()
    while executor.diagnostics[key] < expected:
        remaining = deadline - time.monotonic()
        assert remaining > 0, dict(executor.diagnostics)
        yielded.wait(min(0.001, remaining))


def details(path: Path) -> list[Any]:
    database = Database.open_read_only(path)
    try:
        ids = database.connection.execute("SELECT run_id FROM counterfactual_runs").fetchall()
        return [Repository(database).counterfactual_run_detail(row[0]) for row in ids]
    finally:
        database.close()


def test_artifact_load_validates_setup_inputs(tmp_path: Path, artifact: ShadowArtifact) -> None:
    assert artifact.prompt.prompt_hash
    assert artifact.outcome_policy.policy_hash
    malformed = tmp_path / "prompt.md"
    malformed.write_text("invalid")
    with pytest.raises(ValueError):
        load_shadow_artifact(malformed, Path("glassbox/eval/policies/reconciliation_v2.toml"))
    with pytest.raises(ValueError, match="invalid_policy"):
        load_shadow_artifact(Path("glassbox/eval/prompts/counterfactual_v1.md"), tmp_path / "no")


@pytest.mark.parametrize("flag", [None, "0", "true", " 1"])
def test_disabled_returns_without_loading_config_or_calling_provider(
    executors: Any, artifact: ShadowArtifact, monkeypatch: pytest.MonkeyPatch, flag: str | None,
) -> None:
    def forbidden(*args: Any) -> Any:
        pytest.fail("disabled submission loaded configuration or provider")

    monkeypatch.setattr(shadow, "load_counterfactual_config", forbidden)
    executor = executors(
        environ={} if flag is None else {"GLASSBOX_SHADOW_ENABLED": flag},
        provider_factory=forbidden,
    )
    assert executor.submit(DECISION_ID, artifact) is None
    assert executor.diagnostics["disabled"] == 1
    assert set(executor.diagnostics) == COUNTERS


def test_configuration_errors_are_fail_open(executors: Any, artifact: ShadowArtifact) -> None:
    executor = executors(environ={"GLASSBOX_SHADOW_ENABLED": "1"})
    assert executor.submit(DECISION_ID, artifact) is None
    assert executor.diagnostics["configuration_unavailable"] == 1


def test_unexpected_admission_exception_is_contained(
    executors: Any, artifact: ShadowArtifact, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*args: Any) -> Any:
        raise OSError("unreadable configuration")

    monkeypatch.setattr(shadow, "load_counterfactual_config", broken)
    executor = executors()
    before = executor.diagnostics
    executor.submit(DECISION_ID, artifact)
    assert before["configuration_unavailable"] == 0
    assert executor.diagnostics["configuration_unavailable"] == 1
    with pytest.raises(TypeError):
        executor.diagnostics["completed"] = 1  # type: ignore[index]


def test_remote_gate_is_checked_on_every_submission(
    executors: Any, artifact: ShadowArtifact, tmp_path: Path,
) -> None:
    _seed(tmp_path / "events.sqlite3")
    environ = ENV | {"GLASSBOX_COUNTERFACTUAL_OLLAMA_URL": "https://remote.example"}
    provider = FakeProvider([_answer()])
    executor = executors(environ=environ, provider_factory=lambda _: provider)
    executor.submit(DECISION_ID, artifact)
    assert executor.diagnostics["egress_not_authorized"] == 1
    assert provider.calls == []
    environ["GLASSBOX_SHADOW_ALLOW_EGRESS"] = "1"
    executor.submit(DECISION_ID, artifact)
    wait_for_counter(executor, "completed")
    del environ["GLASSBOX_SHADOW_ALLOW_EGRESS"]
    executor.submit(DECISION_ID, artifact)
    assert executor.diagnostics["egress_not_authorized"] == 2
    assert len(provider.calls) == 1


@pytest.mark.parametrize("existing_database", [False, True])
def test_missing_source_never_constructs_provider(
    executors: Any, artifact: ShadowArtifact, tmp_path: Path, existing_database: bool,
) -> None:
    path = tmp_path / "events.sqlite3"
    if existing_database:
        Database.open(path).close()
    factories: list[Any] = []
    clock = Clock()
    executor = executors(
        monotonic_clock=clock.monotonic, sleep=clock.advance,
        provider_factory=lambda config: factories.append(config),
    )
    executor.submit(DECISION_ID, artifact)
    wait_for_counter(executor, "source_decision_unavailable")
    assert clock.now == 5.0
    assert factories == []
    assert path.exists() == existing_database


def test_persisted_source_writes_one_live_run_and_deadline(
    executors: Any, artifact: ShadowArtifact, tmp_path: Path,
) -> None:
    path = tmp_path / "events.sqlite3"
    _seed(path, count=2, outcome=True)
    provider = FakeProvider([_answer()])
    executor = executors(provider_factory=lambda _: provider)
    executor.submit(DECISION_ID, artifact)
    wait_for_counter(executor, "completed")
    stored, = details(path)
    assert stored.run.execution_mode == "live_shadow"
    assert stored.run.selected_count == 1
    result, = stored.results
    assert result.decision_id == DECISION_ID
    assert stored.run.decided_since == DECIDED
    assert result.outcome_status == "outcome_not_applicable"
    assert result.outcome_id is None
    assert provider.timeouts == [10.0]
    assert "ORIGINAL_RATIONALE" not in provider.calls[0][1]


def test_poll_sees_later_persistence_and_closes_each_connection(
    executors: Any, artifact: ShadowArtifact, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "events.sqlite3"
    Database.open(path).close()
    opened: list[Database] = []
    original = Database.open_read_only

    def tracked(*args: Any, **kwargs: Any) -> Database:
        database = original(*args, **kwargs)
        opened.append(database)
        return database

    monkeypatch.setattr(Database, "open_read_only", tracked)
    clock = Clock()

    def persist(seconds: float) -> None:
        clock.advance(seconds)
        _seed(path)

    executor = executors(monotonic_clock=clock.monotonic, sleep=persist)
    executor.submit(DECISION_ID, artifact)
    wait_for_counter(executor, "completed")
    assert len(opened) == 2
    for database in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            database.connection.execute("SELECT 1")


def test_read_failure_closes_connection_and_never_constructs_provider(
    executors: Any, artifact: ShadowArtifact, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(tmp_path / "events.sqlite3")
    opened: list[Database] = []
    factories: list[Any] = []
    original = Database.open_read_only

    def tracked(*args: Any, **kwargs: Any) -> Database:
        database = original(*args, **kwargs)
        opened.append(database)
        return database

    def broken(*args: Any) -> Any:
        raise sqlite3.OperationalError("unreadable decision")

    monkeypatch.setattr(Database, "open_read_only", tracked)
    monkeypatch.setattr(Repository, "counterfactual_candidate", broken)
    executor = executors(provider_factory=lambda config: factories.append(config))
    executor.submit(DECISION_ID, artifact)
    wait_for_counter(executor, "storage_failure")
    assert factories == []
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].connection.execute("SELECT 1")


def test_candidate_read_after_poll_deadline_never_egresses(
    executors: Any, artifact: ShadowArtifact, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed(tmp_path / "events.sqlite3")
    original = Repository.counterfactual_candidate
    clock = Clock()
    factories: list[Any] = []

    def delayed(*args: Any) -> Any:
        clock.advance(5)
        return original(*args)

    monkeypatch.setattr(Repository, "counterfactual_candidate", delayed)
    executor = executors(
        monotonic_clock=clock.monotonic, sleep=clock.advance,
        provider_factory=lambda config: factories.append(config),
    )
    executor.submit(DECISION_ID, artifact)
    wait_for_counter(executor, "source_decision_unavailable")
    assert factories == []


@pytest.mark.parametrize(("response", "counter", "code"), [
    (CounterfactualProviderTimeout("deadline"), "provider_timeout", "provider_timeout"),
    (RuntimeError("transport"), "provider_failure", "provider_failure"),
    ("invalid", "parse_failure", "invalid_response"),
])
def test_returned_failure_is_persisted_and_counted(
    executors: Any, artifact: ShadowArtifact, tmp_path: Path,
    response: str | Exception, counter: str, code: str,
) -> None:
    path = tmp_path / "events.sqlite3"
    _seed(path)
    executor = executors(provider_factory=lambda _: FakeProvider([response]))
    executor.submit(DECISION_ID, artifact)
    wait_for_counter(executor, counter)
    stored, = details(path)
    assert stored.results[0].failure_code == code
    assert stored.results[0].outcome_status == "case_failed"
    assert executor.diagnostics["completed"] == 0
    assert executor.diagnostics["storage_failure"] == 0


def test_provider_construction_failure_is_provider_failure(
    executors: Any, artifact: ShadowArtifact, tmp_path: Path,
) -> None:
    _seed(tmp_path / "events.sqlite3")

    def broken(config: Any) -> Any:
        raise RuntimeError("factory failed")

    executor = executors(provider_factory=broken)
    executor.submit(DECISION_ID, artifact)
    wait_for_counter(executor, "provider_failure")
    assert details(tmp_path / "events.sqlite3")[0].results[0].failure_code == "provider_unavailable"


def test_provider_boundary_keeps_timed_out_call_from_overlapping_next_job(
    executors: Any, artifact: ShadowArtifact, tmp_path: Path,
) -> None:
    _seed(tmp_path / "events.sqlite3")
    release, finished = threading.Event(), threading.Event()
    calls: list[float | None] = []

    class Transport:
        def judge(self, *args: Any, timeout_seconds: float | None = None) -> str:
            calls.append(timeout_seconds)
            try:
                assert release.wait(2)
                return _answer()
            finally:
                finished.set()

    class ShortTestDeadline:
        def __init__(self, config: CounterfactualConfig) -> None:
            self.provider = create_counterfactual_provider(config, transport_factory=lambda _: (
                Transport()
            ))

        def complete(self, system: str, user: str, *, timeout_seconds: float | None = None) -> str:
            assert timeout_seconds == 10.0
            # Exercise the real Task 2 boundary with a short test-only deadline.
            return self.provider.complete(system, user, timeout_seconds=0.01)

    executor = executors(provider_factory=ShortTestDeadline)
    try:
        executor.submit(DECISION_ID, artifact)
        wait_for_counter(executor, "provider_timeout")
        executor.submit(DECISION_ID, artifact)
        wait_for_counter(executor, "provider_timeout", 2)
        assert calls == [0.01]
        assert all(detail.results[0].outcome_status == "case_failed" for detail in details(
            tmp_path / "events.sqlite3"
        ))
    finally:
        release.set()
        assert finished.wait(2)
        # The transport signals just before returning; wait for boundary cleanup
        # too so no unfinished provider call leaks into another test.
        assert counterfactual_provider._BOUNDED_REQUEST_SLOT.acquire(timeout=2)
        counterfactual_provider._BOUNDED_REQUEST_SLOT.release()


def test_thread_start_failure_is_fail_open_and_does_not_consume_budget(
    executors: Any, artifact: ShadowArtifact, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _seed(tmp_path / "events.sqlite3")
    original = shadow.Thread

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("thread unavailable")

    monkeypatch.setattr(shadow, "Thread", broken)
    executor = executors()
    executor.submit(DECISION_ID, artifact)
    assert executor.diagnostics["configuration_unavailable"] == 1
    monkeypatch.setattr(shadow, "Thread", original)
    for index in range(10):
        executor.submit(DECISION_ID, artifact)
        wait_for_counter(executor, "completed", index + 1)


def test_database_write_failure_is_contained_and_worker_recovers(
    executors: Any, artifact: ShadowArtifact, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "events.sqlite3"
    _seed(path)
    original = Repository.record_counterfactual_run

    def broken(*args: Any) -> None:
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(Repository, "record_counterfactual_run", broken)
    executor = executors()
    executor.submit(DECISION_ID, artifact)
    wait_for_counter(executor, "storage_failure")
    assert details(path) == []
    monkeypatch.setattr(Repository, "record_counterfactual_run", original)
    executor.submit(DECISION_ID, artifact)
    wait_for_counter(executor, "completed")


def test_one_active_one_queued_across_instances_and_submission_is_nonblocking(
    executors: Any, artifact: ShadowArtifact, tmp_path: Path,
) -> None:
    _seed(tmp_path / "events.sqlite3")
    entered, release, submitted = threading.Event(), threading.Event(), threading.Event()

    class BlockingProvider:
        def complete(self, *args: Any, **kwargs: Any) -> str:
            entered.set()
            assert release.wait(2)
            return _answer()

    first = executors(provider_factory=lambda _: BlockingProvider())
    second = executors()
    first.submit(DECISION_ID, artifact)
    assert entered.wait(2)
    try:
        def submit() -> None:
            assert second.submit(DECISION_ID, artifact) is None
            assert second.submit(DECISION_ID, artifact) is None
            submitted.set()

        caller = threading.Thread(target=submit, daemon=True)
        caller.start()
        assert submitted.wait(0.5), "production submission waited for the provider"
        caller.join(1)
        assert second.diagnostics["queue_full"] == 1
        first.shutdown(timeout=0)
        assert second.diagnostics["completed"] == 0
    finally:
        release.set()
    wait_for_counter(first, "completed")
    wait_for_counter(second, "completed")
    first.submit(DECISION_ID, artifact)
    assert first.diagnostics["disabled"] == 1


def test_ten_accepted_jobs_is_process_lifetime_cap_across_instances(
    executors: Any, artifact: ShadowArtifact, tmp_path: Path,
) -> None:
    _seed(tmp_path / "events.sqlite3")
    first, second = executors(), executors()
    for index in range(10):
        executor = first if index % 2 == 0 else second
        executor.submit(DECISION_ID, artifact)
        wait_for_counter(executor, "completed", index // 2 + 1)
    first.shutdown(timeout=1)
    third = executors()
    third.submit(DECISION_ID, artifact)
    assert third.diagnostics["lifetime_cap"] == 1
    assert len(details(tmp_path / "events.sqlite3")) == 10
