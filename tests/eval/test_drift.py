"""Pure drift policy and metric contract tests."""

import json
import math
import random
from collections.abc import Iterator, Sequence
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, stdev

import pytest

from glassbox.eval.drift import (
    DecisionSample,
    DriftPolicy,
    DriftPolicyError,
    TraceSample,
    _material_from_record,
    _status,
    calculate_baseline,
    calculate_report,
    create_baseline,
    load_policy,
    policy_hash,
    run_drift_report,
)
from glassbox.store import Database, Repository
from glassbox.store.repository import DriftBaselineRecord

POLICY_PATH = Path(__file__).resolve().parents[2] / "glassbox/eval/policies/drift_v1.toml"
BASELINE_AT = datetime(2026, 2, 1, tzinfo=UTC)
RECENT_AT = datetime(2026, 7, 30, tzinfo=UTC)
AS_OF = datetime(2026, 8, 1, tzinfo=UTC)
BASELINE_START = datetime(2026, 1, 1, tzinfo=UTC)
BASELINE_END = datetime(2026, 6, 1, tzinfo=UTC)
V2_POLICY_TEXT = """policy_version = "drift_v2"
recent_duration = "7d"

[confidence]
population = "decision"
algorithm = "psi"
minimum_baseline_samples = 500
minimum_recent_samples = 200
bin_boundaries = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
smoothing = 0.5
warning_threshold = 0.10
alert_threshold = 0.16

[decision_type]
population = "decision"
algorithm = "psi"
minimum_baseline_samples = 500
minimum_recent_samples = 200
smoothing = 0.5
warning_threshold = 0.10
alert_threshold = 0.16
"""
LATENCY_POLICY_TEXT = """policy_version = "drift_v2"
recent_duration = "7d"

[trace_latency_ms]
population = "trace"
algorithm = "cusum"
minimum_baseline_samples = 200
minimum_recent_samples = 100
transform = "log1p"
evaluation_samples = 100
reference_value = 0.5
warning_threshold = 5.2
alert_threshold = 7.0
"""


def _policy_text(tmp_path: Path, text: str) -> DriftPolicy:
    path = tmp_path / "policy.toml"
    path.write_text(text, encoding="utf-8")
    return load_policy(path)


@pytest.fixture
def v2_policy(tmp_path: Path) -> DriftPolicy:
    return _policy_text(tmp_path, V2_POLICY_TEXT)


@pytest.fixture
def latency_policy(tmp_path: Path) -> DriftPolicy:
    return _policy_text(tmp_path, LATENCY_POLICY_TEXT)


def test_two_signal_policy_allows_missing_known_signals(v2_policy: DriftPolicy) -> None:
    assert tuple(signal.name for signal in v2_policy.signals) == ("confidence", "decision_type")
    assert v2_policy.signal("confidence").smoothing == 0.5
    assert v2_policy.signal("confidence").transform == "none"
    assert v2_policy.signal("confidence").evaluation_samples is None
    assert not hasattr(v2_policy, "baseline_start")
    with pytest.raises(KeyError):
        v2_policy.signal("trace_latency_ms")


@pytest.mark.parametrize("names", [(), ("confidence", "confidence")])
def test_policy_constructor_rejects_empty_or_duplicate_signals(
    v2_policy: DriftPolicy, names: tuple[str, ...]
) -> None:
    with pytest.raises(DriftPolicyError):
        replace(v2_policy, signals=tuple(v2_policy.signal(name) for name in names))


def test_checked_in_v2_policy_matches_calibrated_fixture(v2_policy: DriftPolicy) -> None:
    assert load_policy(POLICY_PATH.with_name("drift_v2.toml")) == v2_policy


def test_v2_baseline_creation_requires_explicit_windows(
    tmp_path: Path, v2_policy: DriftPolicy
) -> None:
    path = tmp_path / "drift.sqlite3"
    _seed_drift(path)
    with pytest.raises(DriftPolicyError, match="baseline_start and baseline_end are required"):
        create_baseline(path, "agent-a", v2_policy, supersede=False, clock=lambda: AS_OF)


@pytest.mark.parametrize("trace_only", [False, True])
def test_stored_optional_signal_material_reconstructs_only_enabled_signals(
    v2_policy: DriftPolicy, latency_policy: DriftPolicy, trace_only: bool
) -> None:
    policy = latency_policy if trace_only else v2_policy
    decision_samples = () if trace_only else decisions(0.5, "review", 500)
    trace_samples = traces(10.0, 1.0, 200) if trace_only else ()
    baseline = _baseline_v2(policy, decision_samples, trace_samples)
    record = DriftBaselineRecord(
        baseline_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        agent_name="a",
        policy_version=policy.policy_version,
        policy_hash=policy.policy_hash,
        baseline_start=BASELINE_START,
        baseline_end=BASELINE_END,
        created_at=AS_OF,
        version_counts={
            "versions": {"v1": {"decisions": len(decision_samples), "traces": len(trace_samples)}},
            "decisions_per_trace": baseline.decisions_per_trace,
        },
        reference=json.loads(
            json.dumps({signal.name: asdict(signal) for signal in baseline.signals})
        ),
        supersedes_baseline_id=None,
    )
    restored = _material_from_record(record, policy)
    assert restored.signals == baseline.signals
    assert restored.decisions_per_trace == baseline.decisions_per_trace
    extra = dict(record.reference)
    extra["trace_cost_usd"] = {}
    with pytest.raises(DriftPolicyError, match="invalid reference"):
        _material_from_record(replace(record, reference=extra), policy)


@pytest.mark.parametrize(
    "text",
    [
        'policy_version = "drift_v2"\nrecent_duration = "7d"\n',
        V2_POLICY_TEXT.replace("[decision_type]", "[unknown_signal]"),
        V2_POLICY_TEXT.replace('recent_duration = "7d"', 'baseline_start = "2026-01-01"'),
        V2_POLICY_TEXT.replace("smoothing = 0.5", 'transform = "log1p"\nsmoothing = 0.5'),
        V2_POLICY_TEXT.replace("smoothing = 0.5", "evaluation_samples = 100\nsmoothing = 0.5"),
        LATENCY_POLICY_TEXT.replace('transform = "log1p"\n', ""),
        LATENCY_POLICY_TEXT.replace("evaluation_samples = 100\n", ""),
        LATENCY_POLICY_TEXT.replace("evaluation_samples = 100", "evaluation_samples = 0"),
        LATENCY_POLICY_TEXT.replace('transform = "log1p"', 'transform = "sqrt"'),
    ],
)
def test_v2_policy_rejects_empty_unknown_or_invalid_signal_fields(
    tmp_path: Path, text: str
) -> None:
    with pytest.raises(DriftPolicyError):
        _policy_text(tmp_path, text)


def _baseline_v2(policy: DriftPolicy, samples=(), trace_samples=()):  # type: ignore[no-untyped-def]
    return calculate_baseline(
        policy, samples, trace_samples, baseline_start=BASELINE_START, baseline_end=BASELINE_END
    )


def test_latency_uses_the_100_most_recent_valid_samples_after_log_transform(
    latency_policy: DriftPolicy,
) -> None:
    raw_baseline = [math.expm1(2.0 + i % 2) for i in range(200)]
    baseline_traces = tuple(
        TraceSample("a", "v1", value, None, BASELINE_AT, 1) for value in raw_baseline
    )
    baseline = _baseline_v2(latency_policy, trace_samples=baseline_traces)
    old = baseline.signal("trace_latency_ms")
    assert old.mean == pytest.approx(mean(math.log1p(value) for value in raw_baseline))
    assert old.standard_deviation == pytest.approx(
        stdev(math.log1p(value) for value in raw_baseline)
    )
    ordered = tuple(
        TraceSample(
            "a", "v2", 1_000_000.0 if i == 0 else 10.0, None, RECENT_AT + timedelta(seconds=i), 1
        )
        for i in range(101)
    )
    missing = TraceSample("a", "v2", None, None, RECENT_AT + timedelta(seconds=200), 1)
    report = calculate_report(latency_policy, baseline, (), (missing, *reversed(ordered)), AS_OF)
    signal = report.signal("trace_latency_ms")
    assert signal.recent_count == 101
    assert signal.details["evaluation_count"] == 100
    assert signal.details["recent_values"] == pytest.approx([math.log1p(10.0)] * 100)
    assert len(signal.details["cusum_path"]) == 100
    assert signal.details["recent_mean"] == pytest.approx(mean([1_000_000.0] + [10.0] * 100))
    assert signal.details["recent_median"] == 10.0
    assert signal.details["cusum_peak"] == signal.value
    assert "cusum_final_positive" in signal.details
    assert "cusum_final_negative" in signal.details


def test_zero_variance_baseline_has_no_variance_reason(latency_policy: DriftPolicy) -> None:
    baseline = _baseline_v2(latency_policy, trace_samples=traces(10.0, 1.0, 200))
    report = calculate_report(
        latency_policy, baseline, (), traces(30.0, 4.0, 100, recent=True), AS_OF
    )
    assert report.signal("trace_latency_ms").reason == "baseline_has_no_variance"
    assert (report.status, report.reason) == ("insufficient_data", "baseline_has_no_variance")


def test_disabled_known_signals_are_absent_from_persistable_report_rows(
    v2_policy: DriftPolicy,
) -> None:
    baseline = _baseline_v2(v2_policy, decisions(0.5, "review", 500))
    report = calculate_report(v2_policy, baseline, decisions(0.5, "review", 200), (), AS_OF)
    assert [signal.name for signal in baseline.signals] == ["confidence", "decision_type"]
    assert [signal.to_dict()["name"] for signal in report.signals] == [
        "confidence",
        "decision_type",
    ]
    assert set(report.to_dict()["signals"]) == {"confidence", "decision_type"}
    assert report.status == "healthy"


def test_decision_type_keeps_top_nine_categories_with_stable_ties(v2_policy: DriftPolicy) -> None:
    samples = tuple(
        DecisionSample("a", "v1", 0.5, name, BASELINE_AT)
        for name, count in [("large", 100), ("other", 80)]
        + [(f"kind-{i:02d}", 40) for i in range(10)]
        for _ in range(count)
    )
    baseline = _baseline_v2(v2_policy, samples)
    signal = baseline.signal("decision_type")
    assert signal.labels == ("large", *(f"kind-{i:02d}" for i in range(8)), "other")
    assert signal.counts == (100, *([40] * 8), 160)
    report = calculate_report(
        v2_policy, baseline, decisions(0.5, "kind-09", 200, recent=True), (), AS_OF
    )
    assert report.signal("decision_type").details["recent_counts"] == [0] * 9 + [200]


def test_v2_stationary_alert_rates_are_at_most_five_percent(
    v2_policy: DriftPolicy,
    latency_policy: DriftPolicy,
) -> None:
    rng = random.Random(20261010)
    alerts = {"confidence": 0, "decision_type": 0, "trace_latency_ms": 0}
    for _ in range(1_000):
        baseline_decisions = tuple(
            DecisionSample("a", "v1", rng.random(), f"kind-{rng.randrange(10)}", BASELINE_AT)
            for _ in range(500)
        )
        recent_decisions = tuple(
            DecisionSample("a", "v2", rng.random(), f"kind-{rng.randrange(10)}", RECENT_AT)
            for _ in range(200)
        )
        baseline = _baseline_v2(v2_policy, baseline_decisions)
        report = calculate_report(v2_policy, baseline, recent_decisions, (), AS_OF)
        for signal in report.signals:
            alerts[signal.name] += signal.status == "drift_detected"
        baseline_traces = tuple(
            TraceSample("a", "v1", rng.lognormvariate(4.0, 0.5), None, BASELINE_AT, 1)
            for _ in range(200)
        )
        recent_traces = tuple(
            TraceSample(
                "a", "v2", rng.lognormvariate(4.0, 0.5), None, RECENT_AT + timedelta(seconds=i), 1
            )
            for i in range(100)
        )
        baseline = _baseline_v2(latency_policy, trace_samples=baseline_traces)
        report = calculate_report(latency_policy, baseline, (), recent_traces, AS_OF)
        alerts["trace_latency_ms"] += report.status == "drift_detected"
    assert all(count / 1_000 <= 0.05 for count in alerts.values()), alerts


def test_v2_shifted_distributions_still_detect_drift(
    v2_policy: DriftPolicy,
    latency_policy: DriftPolicy,
) -> None:
    baseline = _baseline_v2(v2_policy, decisions(0.1, "review", 500))
    report = calculate_report(
        v2_policy, baseline, decisions(0.9, "order", 200, recent=True), (), AS_OF
    )
    assert all(signal.status == "drift_detected" for signal in report.signals)
    varied = tuple(
        TraceSample("a", "v1", 40.0 if i % 2 else 60.0, None, BASELINE_AT, 1) for i in range(200)
    )
    baseline = _baseline_v2(latency_policy, trace_samples=varied)
    report = calculate_report(
        latency_policy, baseline, (), traces(300.0, 1.0, 100, recent=True), AS_OF
    )
    assert report.status == "drift_detected"


def _seed_drift(
    path: Path, *, baseline_decisions: int = 100, recent_at: datetime = RECENT_AT
) -> None:
    database = Database.open(path)
    try:
        connection = database.connection
        for index in range(50):
            trace_id = f"{index + 1:026X}"
            connection.execute(
                "INSERT INTO traces (trace_id, agent_name, agent_version, started_at, "
                "status, environment, latency_ms, total_cost_usd) "
                "VALUES (?, 'agent-a', 'v1', ?, 'ok', 'dev', ?, ?)",
                (
                    trace_id,
                    BASELINE_AT.isoformat().replace("+00:00", "Z"),
                    9.0 if index % 2 else 11.0,
                    0.9 if index % 2 else 1.1,
                ),
            )
        for index in range(baseline_decisions):
            connection.execute(
                "INSERT INTO decisions (decision_id, trace_id, agent_name, agent_version, "
                "entity_type, entity_id, decision_type, recommendation, rationale, "
                "rationale_citations, confidence, alternatives_considered, decided_at) "
                "VALUES (?, ?, 'agent-a', 'v1', 'sku', 'one', 'review', '{}', 'r', "
                "'[]', 0.05, '[]', ?)",
                (
                    f"{index + 1000:026X}",
                    f"{index % 50 + 1:026X}",
                    BASELINE_AT.isoformat().replace("+00:00", "Z"),
                ),
            )
        for index in range(20):
            trace_id = f"{index + 2000:026X}"
            connection.execute(
                "INSERT INTO traces (trace_id, agent_name, agent_version, started_at, "
                "status, environment, latency_ms, total_cost_usd) "
                "VALUES (?, 'agent-a', 'v2', ?, 'ok', 'dev', 30.0, 4.0)",
                (trace_id, recent_at.isoformat().replace("+00:00", "Z")),
            )
        for index in range(30):
            connection.execute(
                "INSERT INTO decisions (decision_id, trace_id, agent_name, agent_version, "
                "entity_type, entity_id, decision_type, recommendation, rationale, "
                "rationale_citations, confidence, alternatives_considered, decided_at) "
                "VALUES (?, ?, 'agent-a', 'v2', 'sku', 'one', 'order', '{}', 'r', "
                "'[]', 0.95, '[]', ?)",
                (
                    f"{index + 3000:026X}",
                    f"{index % 20 + 2000:026X}",
                    recent_at.isoformat().replace("+00:00", "Z"),
                ),
            )
        connection.commit()
    finally:
        database.close()


def test_run_drift_report_captures_as_of_once_and_persists_exact_snapshot(
    tmp_path: Path, policy, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "drift.sqlite3"
    _seed_drift(path)
    baseline = create_baseline(path, "agent-a", policy, supersede=False, clock=lambda: AS_OF)
    calls = 0

    def clock() -> datetime:
        nonlocal calls
        calls += 1
        return AS_OF

    source_bounds: list[tuple[datetime, datetime]] = []
    original_source = Repository.drift_recent_source_data

    def track_source(self, agent, recent_start, recent_end):  # type: ignore[no-untyped-def]
        source_bounds.append((recent_start, recent_end))
        return original_source(self, agent, recent_start, recent_end)

    monkeypatch.setattr(Repository, "drift_recent_source_data", track_source)

    report = run_drift_report(path, "agent-a", policy, clock=clock, persist=True)
    assert calls == 1
    assert source_bounds == [(AS_OF - policy.recent_duration, AS_OF)]
    assert report.as_of == report.recent_end == AS_OF
    assert report.baseline_id == baseline.baseline_id
    assert report.status == "drift_detected"
    database = Database.open_read_only(path)
    try:
        rows = database.connection.execute("SELECT * FROM drift_runs").fetchall()
        assert len(rows) == 1
        assert rows[0]["baseline_id"] == report.baseline_id
        assert rows[0]["as_of"] == AS_OF.isoformat().replace("+00:00", "Z")
        assert rows[0]["recent_end"] == rows[0]["as_of"]
        assert rows[0]["policy_hash"] == policy.policy_hash
        results = database.connection.execute("SELECT * FROM drift_results").fetchall()
        assert len(results) == 4
        assert {row["signal_name"] for row in results} == set(report.to_dict()["signals"])
        for row in results:
            signal = report.signal(row["signal_name"])
            assert row["status"] == signal.status
            assert row["baseline_count"] == signal.baseline_count
            assert row["recent_count"] == signal.recent_count
            assert row["metric_value"] == signal.value
    finally:
        database.close()

    run_drift_report(path, "agent-a", policy, clock=lambda: AS_OF, persist=False)
    database = Database.open_read_only(path)
    try:
        assert database.connection.execute("SELECT count(*) FROM drift_runs").fetchone()[0] == 1
    finally:
        database.close()


def test_baseline_rejects_insufficient_named_signal_and_duplicate(tmp_path: Path, policy) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "drift.sqlite3"
    _seed_drift(path, baseline_decisions=99)
    with pytest.raises(DriftPolicyError, match="confidence"):
        create_baseline(path, "agent-a", policy, supersede=False, clock=lambda: AS_OF)
    database = Database.open(path)
    try:
        database.connection.execute(
            "INSERT INTO decisions (decision_id, trace_id, agent_name, agent_version, "
            "entity_type, entity_id, decision_type, recommendation, rationale, "
            "rationale_citations, confidence, alternatives_considered, decided_at) "
            "VALUES (?, ?, 'agent-a', 'v1', 'sku', 'one', 'review', '{}', 'r', "
            "'[]', 0.05, '[]', ?)",
            (f"{9999:026X}", f"{1:026X}", BASELINE_AT.isoformat().replace("+00:00", "Z")),
        )
        database.connection.commit()
    finally:
        database.close()
    first = create_baseline(path, "agent-a", policy, supersede=False, clock=lambda: AS_OF)
    with pytest.raises(ValueError):
        create_baseline(path, "agent-a", policy, supersede=False, clock=lambda: AS_OF)
    second = create_baseline(path, "agent-a", policy, supersede=True, clock=lambda: AS_OF)
    assert second.supersedes_baseline_id == first.baseline_id


def test_report_without_matching_baseline_is_in_memory_and_policy_aware(
    tmp_path: Path, policy
) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "drift.sqlite3"
    _seed_drift(path)
    report = run_drift_report(path, "agent-a", policy, clock=lambda: AS_OF, persist=True)
    assert (report.status, report.reason) == ("insufficient_data", "baseline_not_created")
    create_baseline(path, "agent-a", policy, supersede=False, clock=lambda: AS_OF)
    changed_path = tmp_path / "changed.toml"
    changed_path.write_text(
        POLICY_PATH.read_text().replace("warning_threshold = 0.10", "warning_threshold = 0.11")
    )
    changed = load_policy(changed_path)
    report = run_drift_report(path, "agent-a", changed, clock=lambda: AS_OF, persist=True)
    assert (report.status, report.reason) == (
        "insufficient_data",
        "policy_changed_requires_rebaseline",
    )
    database = Database.open_read_only(path)
    try:
        assert database.connection.execute("SELECT count(*) FROM drift_runs").fetchone()[0] == 0
    finally:
        database.close()


@pytest.mark.parametrize(
    "invalid_as_of",
    (
        datetime(2026, 8, 1),
        datetime(2026, 8, 1, tzinfo=timezone(timedelta(hours=-4))),
    ),
)
def test_run_drift_report_rejects_non_utc_clock_without_a_baseline(
    tmp_path: Path, policy, invalid_as_of: datetime
) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "drift.sqlite3"
    _seed_drift(path)
    with pytest.raises(ValueError, match="as_of must be UTC"):
        run_drift_report(path, "agent-a", policy, clock=lambda: invalid_as_of, persist=True)


@pytest.fixture
def policy():  # type: ignore[no-untyped-def]
    return load_policy(POLICY_PATH)


def decisions(confidence: float, kind: str, count: int, *, recent: bool = False):  # type: ignore[no-untyped-def]
    at = RECENT_AT if recent else BASELINE_AT
    version = "v2" if recent else "v1"
    return tuple(DecisionSample("a", version, confidence, kind, at) for _ in range(count))


def traces(latency: float, cost: float, count: int, *, recent: bool = False):  # type: ignore[no-untyped-def]
    at = RECENT_AT if recent else BASELINE_AT
    version = "v2" if recent else "v1"
    return tuple(TraceSample("a", version, latency, cost, at, 2) for _ in range(count))


def test_checked_in_policy_loads_and_hash_is_canonical(tmp_path: Path) -> None:
    original = POLICY_PATH.read_text(encoding="utf-8")
    alternate = tmp_path / "alternate.toml"
    alternate.write_text(
        "\n" + original.replace('policy_version = "drift_v1"', 'policy_version = "drift_v1"  ')
    )
    first = load_policy(POLICY_PATH)
    second = load_policy(alternate)
    assert first.policy_version == "drift_v1"
    assert first.policy_hash == second.policy_hash == policy_hash(first)
    assert len(first.policy_hash) == 64


@pytest.mark.parametrize(
    "old,new",
    [
        ('policy_version = "drift_v1"', 'policy_version = ""'),
        ('recent_duration = "7d"', 'recent_duration = "0d"'),
        ('baseline_start = "2026-01-01T00:00:00Z"', 'baseline_start = "2026-01-01"'),
        ('baseline_end = "2026-06-01T00:00:00Z"', 'baseline_end = "2025-01-01T00:00:00Z"'),
        ("0.0, 0.1, 0.2", "0.0, 0.2, 0.1"),
        ("0.9, 1.0]", "0.9, 0.9]"),
        ("0.0, 0.1, 0.2", "0.01, 0.1, 0.2"),
        ("minimum_recent_samples = 30", "minimum_recent_samples = 0"),
        ("smoothing = 0.0001", "smoothing = 0.0"),
        ("alert_threshold = 0.20", "alert_threshold = 0.05"),
        ("reference_value = 0.5", "reference_value = nan"),
        ("[trace_cost_usd]", "[unknown_signal]"),
    ],
)
def test_policy_rejects_invalid_input(tmp_path: Path, old: str, new: str) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(POLICY_PATH.read_text().replace(old, new), encoding="utf-8")
    with pytest.raises(DriftPolicyError):
        load_policy(path)


def test_confidence_and_decision_type_psi_detect_shift_without_mutating_baseline(policy) -> None:  # type: ignore[no-untyped-def]
    baseline = _baseline_v2(policy, decisions(0.05, "review", 100), ())
    before = repr(baseline)
    recent = decisions(0.95, "order", 30, recent=True)
    report = calculate_report(policy, baseline, recent, (), AS_OF)
    assert report.signal("confidence").status == "drift_detected"
    assert report.signal("decision_type").status == "drift_detected"
    assert repr(baseline) == before


def test_confidence_bins_include_lower_edges_and_one(policy) -> None:  # type: ignore[no-untyped-def]
    samples = (
        DecisionSample("a", "v1", 0.0, "review", BASELINE_AT),
        DecisionSample("a", "v1", 0.1, "review", BASELINE_AT),
        DecisionSample("a", "v1", 1.0, "review", BASELINE_AT),
    )
    baseline = _baseline_v2(policy, samples, ())
    assert baseline.signal("confidence").counts == (1, 1, 0, 0, 0, 0, 0, 0, 0, 1)
    assert baseline.signal("confidence").labels[0] == "[0.0, 0.1)"
    assert baseline.signal("confidence").labels[-1] == "[0.9, 1.0]"


def test_decision_type_uses_other_with_smoothing_for_absent_categories(policy) -> None:  # type: ignore[no-untyped-def]
    baseline = _baseline_v2(policy, decisions(0.5, "review", 100), ())
    report = calculate_report(
        policy, baseline, decisions(0.5, "new_kind", 30, recent=True), (), AS_OF
    )
    signal = report.signal("decision_type")
    assert signal.value is not None and signal.value > 0
    assert signal.details["categories"] == ["review", "other"]
    assert signal.details["recent_counts"] == [0, 30]


def test_cusum_detects_positive_and_negative_trace_shifts(policy) -> None:  # type: ignore[no-untyped-def]
    baseline_samples = tuple(
        TraceSample("a", "v1", 9.0 if i % 2 else 11.0, 0.9 if i % 2 else 1.1, BASELINE_AT, 1)
        for i in range(50)
    )
    baseline = _baseline_v2(policy, (), baseline_samples)
    high = traces(30.0, 4.0, 20, recent=True)
    low = traces(1.0, 0.1, 20, recent=True)
    assert (
        calculate_report(policy, baseline, (), high, AS_OF).signal("trace_latency_ms").status
        == "drift_detected"
    )
    assert (
        calculate_report(policy, baseline, (), low, AS_OF).signal("trace_cost_usd").status
        == "drift_detected"
    )


def test_zero_cusum_variance_is_insufficient_baseline(policy) -> None:  # type: ignore[no-untyped-def]
    baseline = _baseline_v2(policy, (), traces(10.0, 1.0, 50))
    report = calculate_report(policy, baseline, (), traces(30.0, 4.0, 20, recent=True), AS_OF)
    assert report.signal("trace_latency_ms").status == "insufficient_data"
    assert report.signal("trace_latency_ms").reason == "baseline_has_no_variance"
    assert report.status == "insufficient_data"
    assert report.reason == "baseline_window_too_small"  # enabled v1 decision samples are absent


@pytest.mark.parametrize(
    "baseline_decisions,baseline_traces,recent_decisions,recent_traces,reason",
    [
        (99, 50, 30, 20, "baseline_window_too_small"),
        (100, 49, 30, 20, "baseline_window_too_small"),
        (100, 50, 29, 20, "recent_window_too_small"),
        (100, 50, 30, 19, "recent_window_too_small"),
        (99, 50, 29, 20, "baseline_window_too_small"),
    ],
)
def test_insufficient_data_aggregation_precedence(
    policy, baseline_decisions, baseline_traces, recent_decisions, recent_traces, reason
) -> None:  # type: ignore[no-untyped-def]
    varied_baseline_traces = tuple(
        TraceSample("a", "v1", 9.0 if i % 2 else 11.0, 0.9 if i % 2 else 1.1, BASELINE_AT, 1)
        for i in range(baseline_traces)
    )
    baseline = _baseline_v2(
        policy, decisions(0.5, "review", baseline_decisions), varied_baseline_traces
    )
    report = calculate_report(
        policy,
        baseline,
        decisions(0.5, "review", recent_decisions, recent=True),
        traces(10.0, 1.0, recent_traces, recent=True),
        AS_OF,
    )
    assert report.status == "insufficient_data"
    assert report.reason == reason


def test_threshold_boundaries_and_watch(tmp_path: Path) -> None:
    text = POLICY_PATH.read_text()
    text = text.replace("warning_threshold = 0.10", "warning_threshold = 0.0")
    text = text.replace("alert_threshold = 0.20", "alert_threshold = 1.0")
    path = tmp_path / "thresholds.toml"
    path.write_text(text)
    policy = load_policy(path)
    varied = tuple(
        TraceSample("a", "v1", 9.0 if i % 2 else 11.0, 0.9 if i % 2 else 1.1, BASELINE_AT, 1)
        for i in range(50)
    )
    baseline = _baseline_v2(policy, decisions(0.5, "review", 100), varied)
    same = calculate_report(
        policy,
        baseline,
        decisions(0.5, "review", 30, recent=True),
        traces(10.0, 1.0, 20, recent=True),
        AS_OF,
    )
    assert same.signal("confidence").status == "watch"  # exactly warning boundary
    assert same.status == "watch"

    path.write_text(text.replace("alert_threshold = 1.0", "alert_threshold = 0.0"))
    with pytest.raises(DriftPolicyError):
        load_policy(path)


def test_exact_warning_and_alert_boundaries(policy) -> None:  # type: ignore[no-untyped-def]
    signal = policy.signal("confidence")
    assert _status(signal.warning_threshold, signal) == "watch"
    assert _status(signal.alert_threshold, signal) == "drift_detected"


def test_diagnostics_are_immutable_and_json_safe(policy) -> None:  # type: ignore[no-untyped-def]
    baseline = _baseline_v2(policy, decisions(0.5, "review", 100), traces(10.0, 1.0, 50))
    report = calculate_report(
        policy,
        baseline,
        decisions(0.5, "review", 30, recent=True),
        traces(10.0, 1.0, 20, recent=True),
        AS_OF,
    )
    payload = report.to_dict()
    assert payload["baseline_versions"] == {"v1": {"decisions": 100, "traces": 50}}
    assert payload["recent_versions"] == {"v2": {"decisions": 30, "traces": 20}}
    assert payload["baseline_decisions_per_trace"] == 2.0
    assert payload["recent_decisions_per_trace"] == 2.0
    assert len(payload["signals"]) == 4
    assert payload["as_of"] == AS_OF.isoformat()
    with pytest.raises((FrozenInstanceError, AttributeError)):
        report.status = "healthy"
    with pytest.raises(KeyError):
        report.signal("unknown")


def test_large_finite_smoothing_keeps_psi_and_json_finite(tmp_path: Path) -> None:
    path = tmp_path / "large-smoothing.toml"
    path.write_text(
        POLICY_PATH.read_text().replace("smoothing = 0.0001", "smoothing = 1e308"),
        encoding="utf-8",
    )
    policy = load_policy(path)
    baseline = _baseline_v2(policy, decisions(0.5, "known", 100), ())
    report = calculate_report(
        policy, baseline, decisions(0.5, "unknown", 30, recent=True), (), AS_OF
    )
    metric = report.signal("decision_type").value
    assert metric is not None and math.isfinite(metric)
    json.dumps(report.to_dict(), allow_nan=False)


def test_nonfinite_cusum_metric_is_rejected_before_serialization(policy) -> None:  # type: ignore[no-untyped-def]
    baseline_traces = tuple(
        TraceSample("a", "v1", 0.0 if i % 2 else 2e-100, 0.9 if i % 2 else 1.1, BASELINE_AT, 1)
        for i in range(50)
    )
    baseline = _baseline_v2(policy, (), baseline_traces)
    with pytest.raises(ValueError, match="non-finite"):
        calculate_report(policy, baseline, (), traces(1e308, 1.0, 20, recent=True), AS_OF)


def test_oversized_recent_duration_is_policy_error(tmp_path: Path) -> None:
    path = tmp_path / "huge-duration.toml"
    path.write_text(
        POLICY_PATH.read_text().replace(
            'recent_duration = "7d"', 'recent_duration = "1000000000d"'
        ),
        encoding="utf-8",
    )
    with pytest.raises(DriftPolicyError, match="recent_duration"):
        load_policy(path)


def test_decision_categories_count_recent_samples_once(policy) -> None:  # type: ignore[no-untyped-def]
    class CountedSequence(Sequence[DecisionSample]):
        def __init__(self, samples: tuple[DecisionSample, ...]) -> None:
            self.samples = samples
            self.iterations = 0

        def __len__(self) -> int:
            return len(self.samples)

        def __getitem__(self, index: int) -> DecisionSample:
            return self.samples[index]

        def __iter__(self) -> Iterator[DecisionSample]:
            self.iterations += 1
            yield from self.samples

    baseline_decisions = tuple(
        DecisionSample("a", "v1", 0.5, f"kind-{i % 8}", BASELINE_AT) for i in range(100)
    )
    recent = CountedSequence(decisions(0.5, "unseen", 30, recent=True))
    baseline = _baseline_v2(policy, baseline_decisions, ())
    report = calculate_report(policy, baseline, recent, (), AS_OF)
    assert report.signal("decision_type").details["recent_counts"] == [0] * 8 + [30]
    assert recent.iterations <= 3  # confidence, decision type, and version diagnostics
