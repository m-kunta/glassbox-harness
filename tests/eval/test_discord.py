from __future__ import annotations

import hashlib
import json
import socket
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.client import BadStatusLine
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import URLError
from urllib.request import OpenerDirector, ProxyHandler, Request, build_opener

import pytest

from glassbox.eval.discord import _post_json, build_discord_payload, deliver_drift_alert
from glassbox.eval.discord_config import DiscordConfig
from glassbox.eval.drift import DriftReport, DriftSignalReport
from glassbox.store import Database, Repository
from glassbox.store.repository import DriftBaselineInsert, DriftResultInsert, DriftRunInsert

NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FE2"
BASELINE_ID = "01ARZ3NDEKTSV4RRFFQ69G5FE0"
WEBHOOK = "https://discord.com/api/webhooks/123/secret-webhook-token"
READY = DiscordConfig("ready", WEBHOOK, None, None)
DESTINATION_HASH = hashlib.sha256(WEBHOOK.encode()).hexdigest()


class Receiver:
    def __init__(self, responses: list[tuple[int, dict[str, str]]]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, bytes, str | None]] = []
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                receiver.requests.append((self.path, body, self.headers.get("Content-Type")))
                status, headers = receiver.responses[
                    min(len(receiver.requests) - 1, len(receiver.responses) - 1)
                ]
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(b"secret-response-body")

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/webhook"


@contextmanager
def receiver(responses: list[tuple[int, dict[str, str]]]):
    target = Receiver(responses)
    thread = Thread(target=target.server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield target
    finally:
        target.server.shutdown()
        target.server.server_close()
        thread.join(timeout=2)


class RoutingOpener(OpenerDirector):
    """Use real local HTTP while asserting the public URL stays sealed."""

    def __init__(self, target: Receiver, failures: list[Exception] | None = None) -> None:
        super().__init__()
        self.target = target
        self.failures = list(failures or [])
        self.calls: list[tuple[str, float]] = []

    def open(self, request: Request, timeout: float = 2.0):
        self.calls.append((request.full_url, timeout))
        if self.failures:
            raise self.failures.pop(0)
        routed = Request(
            self.target.url,
            data=request.data,
            headers=dict(request.headers),
            method=request.get_method(),
        )
        return build_opener(ProxyHandler({})).open(routed, timeout=timeout)


@pytest.fixture
def repository(tmp_path: Path):
    database = Database.open(tmp_path / "discord.sqlite3")
    repository = Repository(database)
    repository.record_drift_baseline(
        DriftBaselineInsert(
            BASELINE_ID,
            "agent-a",
            "v1",
            "a" * 64,
            NOW - timedelta(days=14),
            NOW - timedelta(days=7),
            NOW,
            {},
            {"confidence": {}},
        ),
        enabled_signal_names=frozenset({"confidence"}),
    )
    repository.record_drift_run(
        DriftRunInsert(
            RUN_ID,
            BASELINE_ID,
            "agent-a",
            "v1",
            "a" * 64,
            NOW,
            NOW - timedelta(days=7),
            NOW,
            "drift_detected",
            None,
            {},
            {},
        ),
        (
            DriftResultInsert(
                "01ARZ3NDEKTSV4RRFFQ69G5FE3",
                "confidence",
                "decision",
                "psi",
                "drift_detected",
                100,
                30,
                0.8,
                0.1,
                0.2,
                {},
            ),
        ),
        enabled_signal_names=frozenset({"confidence"}),
    )
    yield repository
    database.close()


@pytest.fixture
def report() -> DriftReport:
    return DriftReport(
        "drift_detected",
        None,
        NOW,
        NOW - timedelta(days=14),
        NOW - timedelta(days=7),
        NOW - timedelta(days=7),
        NOW,
        "v1",
        "a" * 64,
        "agent-a",
        BASELINE_ID,
        (("sensitive-baseline-version", 1, 1),),
        (("sensitive-recent-version", 1, 1),),
        1.0,
        1.0,
        (
            DriftSignalReport(
                "confidence",
                "decision",
                "psi",
                100,
                30,
                0.8,
                0.1,
                0.2,
                "drift_detected",
                None,
                {
                    "prompt": "sensitive-prompt",
                    "rationale": "sensitive-rationale",
                    "entity": "sensitive-entity",
                    "capture_ref": "sensitive-capture",
                },
            ),
            DriftSignalReport(
                "decision_type",
                "decision",
                "psi",
                100,
                30,
                0.0,
                0.1,
                0.2,
                "healthy",
                None,
                {"labels": ["sensitive-category"]},
            ),
        ),
    )


def rows(repository: Repository):
    return repository._connection.execute("SELECT * FROM discord_deliveries").fetchall()


def test_payload_is_compact_and_excludes_sensitive_details(report: DriftReport) -> None:
    payload = build_discord_payload(report, report_url=None)
    encoded = json.dumps(payload)
    assert "sensitive-" not in encoded
    assert "decision_type" not in encoded
    assert len(encoded) < 1800
    assert all(
        value in encoded
        for value in (
            "confidence",
            "0.8",
            "0.1",
            "0.2",
            "agent-a",
            "v1",
            "a" * 64,
            NOW.isoformat(),
            BASELINE_ID,
        )
    )
    assert payload["allowed_mentions"] == {"parse": []}


@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://localhost:8000/drift", True),
        ("http://[::1]/drift", True),
        ("https://evil.test/drift", False),
        ("http://localhost.evil.test", False),
        (None, False),
    ],
)
def test_payload_revalidates_optional_report_url(report: DriftReport, url, expected) -> None:
    payload = build_discord_payload(report, report_url=url)
    embed = payload["embeds"][0]
    assert ("url" in embed) is expected
    if expected:
        assert embed["url"] == url


@pytest.mark.parametrize("status", ["healthy", "watch", "insufficient_data"])
def test_non_drift_never_claims_or_builds_or_sends(repository, report, monkeypatch, status) -> None:
    monkeypatch.setattr(
        "glassbox.eval.discord.build_discord_payload",
        lambda *args, **kwargs: pytest.fail("built payload before eligibility"),
    )
    outcome = deliver_drift_alert(
        repository,
        replace(report, status=status),
        RUN_ID,
        READY,
        clock=lambda: NOW,
        sleep=lambda _: pytest.fail("sleep"),
    )
    assert (outcome.status, outcome.reason, outcome.delivery_id) == (
        "skipped",
        "not_drift_detected",
        None,
    )
    assert rows(repository) == []


@pytest.mark.parametrize(
    "config",
    [
        DiscordConfig("disabled", None, None, "missing_url"),
        DiscordConfig("invalid", None, None, "invalid_url"),
    ],
)
def test_unready_config_never_claims_or_builds(repository, report, monkeypatch, config) -> None:
    monkeypatch.setattr(
        "glassbox.eval.discord.build_discord_payload",
        lambda *args, **kwargs: pytest.fail("built payload before config"),
    )
    outcome = deliver_drift_alert(
        repository, report, RUN_ID, config, clock=lambda: NOW, sleep=lambda _: pytest.fail("sleep")
    )
    assert (outcome.status, outcome.reason, outcome.delivery_id) == (
        config.status,
        config.reason,
        None,
    )
    assert rows(repository) == []


@pytest.mark.parametrize("status", [200, 204, 299])
def test_initial_success_and_duplicate_skip(repository, report, monkeypatch, status) -> None:
    with receiver([(status, {})]) as target:
        opener = RoutingOpener(target)
        stamps = iter((NOW, NOW + timedelta(seconds=1)))
        outcome = deliver_drift_alert(
            repository,
            report,
            RUN_ID,
            READY,
            clock=lambda: next(stamps),
            sleep=lambda _: pytest.fail("sleep"),
            opener=opener,
        )
        assert (outcome.status, outcome.reason) == ("delivered", None)
        assert opener.calls == [(WEBHOOK, 2.0)]
        assert target.requests[0][2] == "application/json"
        assert RUN_ID in target.requests[0][1].decode()
        row = rows(repository)[0]
        assert (
            row["delivery_id"],
            row["destination_hash"],
            row["status"],
            row["error_category"],
            row["finalized_at"],
        ) == (outcome.delivery_id, DESTINATION_HASH, "delivered", None, "2026-10-24T12:00:01Z")
        assert "secret" not in repr(tuple(row))
        monkeypatch.setattr(
            "glassbox.eval.discord.build_discord_payload",
            lambda *args, **kwargs: pytest.fail("built for duplicate"),
        )
        duplicate = deliver_drift_alert(
            repository,
            report,
            RUN_ID,
            READY,
            clock=lambda: NOW,
            sleep=lambda _: pytest.fail("sleep"),
            opener=opener,
        )
        assert (duplicate.status, duplicate.reason, duplicate.delivery_id) == (
            "skipped",
            "already_delivered",
            outcome.delivery_id,
        )
        assert len(opener.calls) == len(target.requests) == len(rows(repository)) == 1


def test_in_progress_claim_never_builds_or_sends(repository, report, monkeypatch) -> None:
    claim = repository.claim_discord_delivery(RUN_ID, DESTINATION_HASH, now=NOW)
    monkeypatch.setattr(
        "glassbox.eval.discord.build_discord_payload",
        lambda *args, **kwargs: pytest.fail("built for occupied claim"),
    )
    outcome = deliver_drift_alert(
        repository, report, RUN_ID, READY, clock=lambda: NOW, sleep=lambda _: pytest.fail("sleep")
    )
    assert (outcome.status, outcome.reason, outcome.delivery_id) == (
        "skipped",
        "in_progress",
        claim.delivery.delivery_id,
    )


@pytest.mark.parametrize(
    "status,header,delay",
    [
        (500, None, 0.25),
        (429, "1.5", 1.5),
        (503, "2", 2.0),
        (429, "3", 0.25),
        (429, "nan", 0.25),
        (429, "inf", 0.25),
        (429, "-1", 0.25),
        (429, "invalid", 0.25),
        (429, "Wed, 21 Oct 2026 07:28:00 GMT", 0.25),
    ],
)
def test_retries_http_once_with_bounded_delay(repository, report, status, header, delay) -> None:
    headers = {} if header is None else {"Retry-After": header}
    with receiver([(status, headers), (204, {})]) as target:
        sleeps = []
        opener = RoutingOpener(target)
        outcome = deliver_drift_alert(
            repository,
            report,
            RUN_ID,
            READY,
            clock=lambda: NOW,
            sleep=sleeps.append,
            opener=opener,
            timeout_seconds=0.4,
        )
        assert outcome.status == "delivered"
        assert sleeps == [delay]
        assert opener.calls == [(WEBHOOK, 0.4), (WEBHOOK, 0.4)]
        assert len(target.requests) == 2
        assert target.requests[0][1] == target.requests[1][1]
        assert rows(repository)[0]["status"] == "delivered"


@pytest.mark.parametrize(
    "status,category,count",
    [
        (400, "http_4xx", 1),
        (401, "http_4xx", 1),
        (429, "http_429", 2),
        (500, "http_5xx", 2),
        (503, "http_5xx", 2),
    ],
)
def test_final_http_failures_are_normalized_and_two_calls_maximum(
    repository,
    report,
    status,
    category,
    count,
) -> None:
    with receiver([(status, {})]) as target:
        sleeps = []
        opener = RoutingOpener(target)
        outcome = deliver_drift_alert(
            repository, report, RUN_ID, READY, clock=lambda: NOW, sleep=sleeps.append, opener=opener
        )
        assert (outcome.status, outcome.reason) == ("failed", category)
        assert len(opener.calls) == len(target.requests) == count
        assert sleeps == ([0.25] if count == 2 else [])
        row = rows(repository)[0]
        assert (row["status"], row["error_category"], row["delivery_id"]) == (
            "failed",
            category,
            outcome.delivery_id,
        )
        assert "secret" not in repr(tuple(row)) + repr(outcome)


@pytest.mark.parametrize(
    "failure",
    [
        URLError("secret-transport"),
        socket.timeout("secret-timeout"),
        BadStatusLine("secret-malformed-http"),
    ],
)
@pytest.mark.parametrize("recover", [True, False])
def test_transport_failures_retry_once_and_hide_details(
    repository,
    report,
    failure,
    recover,
) -> None:
    with receiver([(204, {})]) as target:
        opener = RoutingOpener(target, [failure] if recover else [failure, failure])
        sleeps = []
        outcome = deliver_drift_alert(
            repository, report, RUN_ID, READY, clock=lambda: NOW, sleep=sleeps.append, opener=opener
        )
        assert len(opener.calls) == 2
        assert len(target.requests) == (1 if recover else 0)
        assert sleeps == [0.25]
        assert (outcome.status, outcome.reason) == (
            ("delivered", None) if recover else ("failed", "transport_error")
        )
        assert "secret" not in repr(outcome) + repr(tuple(rows(repository)[0]))


def test_default_http_helper_bypasses_all_ambient_proxies(monkeypatch) -> None:
    with receiver([(204, {})]) as target, receiver([(500, {})]) as proxy:
        for key in (
            "http_proxy",
            "HTTP_PROXY",
            "https_proxy",
            "HTTPS_PROXY",
            "all_proxy",
            "ALL_PROXY",
        ):
            monkeypatch.setenv(key, proxy.url)
        for key in ("no_proxy", "NO_PROXY"):
            monkeypatch.setenv(key, "")
        status, retry_after = _post_json(target.url, {"content": "local-only"}, timeout_seconds=1.0)
        assert (status, retry_after) == (204, None)
        assert len(target.requests) == 1
        assert proxy.requests == []


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_default_http_helper_does_not_follow_redirects(status: int) -> None:
    with receiver([(204, {})]) as redirected:
        with receiver([(status, {"Location": redirected.url})]) as target:
            actual, _ = _post_json(target.url, {"content": "local-only"}, timeout_seconds=1.0)
            assert actual == status
            assert len(target.requests) == 1
            assert redirected.requests == []


@pytest.mark.parametrize(
    "cap,header,expected", [(0.5, "1", 0.25), (5, "3", 0.25), (0.5, "0.5", 0.5), (2, "0", 0)]
)
def test_retry_delay_respects_caller_and_absolute_caps(repository, report, cap, header, expected):
    with receiver([(429, {"Retry-After": header}), (204, {})]) as target:
        sleeps = []
        outcome = deliver_drift_alert(
            repository,
            report,
            RUN_ID,
            READY,
            clock=lambda: NOW,
            sleep=sleeps.append,
            opener=RoutingOpener(target),
            retry_delay_cap_seconds=cap,
        )
        assert outcome.status == "delivered"
        assert sleeps == [expected]


def test_delivery_includes_only_valid_optional_report_link(repository, report) -> None:
    with receiver([(204, {})]) as target:
        config = replace(READY, report_url="http://localhost:8000/drift")
        deliver_drift_alert(
            repository,
            report,
            RUN_ID,
            config,
            clock=lambda: NOW,
            sleep=lambda _: pytest.fail("sleep"),
            opener=RoutingOpener(target),
        )
        assert json.loads(target.requests[0][1])["embeds"][0]["url"] == config.report_url


def test_failed_delivery_can_be_claimed_by_a_later_invocation(repository, report) -> None:
    with receiver([(500, {}), (500, {}), (204, {})]) as target:
        opener = RoutingOpener(target)
        failed = deliver_drift_alert(
            repository,
            report,
            RUN_ID,
            READY,
            clock=lambda: NOW,
            sleep=lambda _: None,
            opener=opener,
        )
        delivered = deliver_drift_alert(
            repository,
            report,
            RUN_ID,
            READY,
            clock=lambda: NOW,
            sleep=lambda _: pytest.fail("sleep"),
            opener=opener,
        )
        assert (failed.status, delivered.status) == ("failed", "delivered")
        assert failed.delivery_id != delivered.delivery_id
        history = rows(repository)
        assert [(row["status"], row["attempt_number"]) for row in history] == [
            ("failed", 1),
            ("delivered", 2),
        ]
        assert len(opener.calls) == len(target.requests) == 3
