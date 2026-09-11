from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from glassbox.events import DecisionEvent, EvidenceEvent, SpanEvent, TraceEvent
from glassbox.store import Database, Repository
from glassbox.web.server import create_app, load_server_config, run_server

TOKEN = "t" * 32


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


def app_for(clock: Clock, tmp_path: Path, database_path: Path | None = None):
    database_path = database_path or tmp_path / "glassbox.sqlite3"
    if not database_path.exists():
        database = Database.open(database_path)
        database.close()
    config = load_server_config(
        environ={"GLASSBOX_LOCAL_ACCESS_TOKEN": TOKEN, "GLASSBOX_DATABASE": str(database_path)},
        host="127.0.0.1",
        port=8787,
    )
    return create_app(config, clock=clock)


def seeded_database(tmp_path: Path) -> tuple[Path, str, str]:
    database_path = tmp_path / "seeded.sqlite3"
    database = Database.open(database_path)
    trace_id = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    span_id = "01ARZ3NDEKTSV4RRFFQ69G5FAW"
    decision_id = "01ARZ3NDEKTSV4RRFFQ69G5FAX"
    timestamp = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
    try:
        repository = Repository(database)
        repository.write_event(
            TraceEvent(
                trace_id=trace_id,
                agent_name="planner",
                agent_version="v1",
                started_at=timestamp,
                environment="dev",
            )
        )
        repository.write_event(
            SpanEvent(
                span_id=span_id,
                trace_id=trace_id,
                name="recommendation model",
                span_kind="llm",
                started_at=timestamp,
            )
        )
        repository.write_event(
            DecisionEvent(
                decision_id=decision_id,
                trace_id=trace_id,
                agent_name="planner",
                agent_version="v1",
                entity_type="sku",
                entity_id="sku-1",
                decision_type="replenish",
                recommendation={"action": "order"},
                rationale="Inventory is low.",
                rationale_citations=("inventory",),
                confidence=0.8,
                alternatives_considered=("hold",),
                decided_at=timestamp,
            )
        )
        repository.write_event(
            EvidenceEvent(
                evidence_id="inventory",
                decision_id=decision_id,
                source_system="erp",
                source_ref="inventory/sku-1",
                field_name="available_units",
                field_value=2,
                weight=1.0,
                retrieved_at=timestamp,
            )
        )
    finally:
        database.close()
    return database_path, decision_id, trace_id


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_load_server_config_accepts_exact_loopback_hosts(host: str) -> None:
    config = load_server_config(
        host=host, port=8787, environ={"GLASSBOX_LOCAL_ACCESS_TOKEN": TOKEN}
    )

    assert config.host == host


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "localhost", "192.168.1.8"])
def test_invalid_host_fails_before_uvicorn_is_called(
    host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def unexpected_run(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("glassbox.web.server.uvicorn.run", unexpected_run)

    with pytest.raises(ValueError, match="loopback"):
        run_server(host=host, port=8787, environ={"GLASSBOX_LOCAL_ACCESS_TOKEN": TOKEN})

    assert called is False


@pytest.mark.parametrize("token", [None, "", "x" * 31])
def test_missing_or_short_token_fails_before_startup(token: str | None) -> None:
    environ = {} if token is None else {"GLASSBOX_LOCAL_ACCESS_TOKEN": token}

    with pytest.raises(ValueError, match="GLASSBOX_LOCAL_ACCESS_TOKEN"):
        load_server_config(host="127.0.0.1", port=8787, environ=environ)


def test_non_positive_session_timeout_is_rejected() -> None:
    with pytest.raises(ValueError, match="session_idle_timeout"):
        load_server_config(
            host="127.0.0.1",
            port=8787,
            environ={"GLASSBOX_LOCAL_ACCESS_TOKEN": TOKEN},
            session_idle_timeout=timedelta(),
        )


def test_serve_command_dispatches_validated_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    from glassbox import cli

    received: dict[str, object] = {}

    def fake_run_server(*, host: str, port: int) -> None:
        received.update(host=host, port=port)

    monkeypatch.setattr(cli, "run_server", fake_run_server)

    assert cli.main(["serve", "--host", "::1", "--port", "8788"]) == 0
    assert received == {"host": "::1", "port": 8788}


def test_health_is_public_and_contains_no_decision_data(tmp_path: Path) -> None:
    response = TestClient(app_for(Clock(), tmp_path), base_url="http://127.0.0.1").get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_failed_login_is_generic_and_does_not_set_a_cookie(tmp_path: Path) -> None:
    response = TestClient(app_for(Clock(), tmp_path), base_url="http://127.0.0.1").post(
        "/login", data={"access_token": "wrong"}
    )

    assert response.status_code == 401
    assert "glassbox_session" not in response.headers.get("set-cookie", "")
    assert "wrong" not in response.text


def test_successful_login_sets_the_intended_cookie_attributes(tmp_path: Path) -> None:
    client = TestClient(app_for(Clock(), tmp_path), base_url="http://127.0.0.1")

    response = client.post("/login", data={"access_token": TOKEN}, follow_redirects=False)

    cookie = response.headers["set-cookie"]
    assert response.status_code == 303
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie
    assert TOKEN not in cookie


def test_protected_route_redirects_without_session_and_refreshes_with_session(
    tmp_path: Path,
) -> None:
    clock = Clock()
    client = TestClient(app_for(clock, tmp_path), base_url="http://127.0.0.1")

    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    client.post("/login", data={"access_token": TOKEN})
    clock.value += timedelta(minutes=29)
    assert client.get("/").status_code == 200
    clock.value += timedelta(minutes=29)
    assert client.get("/", follow_redirects=False).status_code == 200


def test_idle_session_is_rejected_after_its_timeout(tmp_path: Path) -> None:
    clock = Clock()
    client = TestClient(app_for(clock, tmp_path), base_url="http://127.0.0.1")
    client.post("/login", data={"access_token": TOKEN})
    clock.value += timedelta(minutes=30, seconds=1)

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_authenticated_decision_and_trace_routes_render_persisted_telemetry(tmp_path: Path) -> None:
    database_path, decision_id, trace_id = seeded_database(tmp_path)
    client = TestClient(
        app_for(Clock(), tmp_path, database_path), base_url="http://127.0.0.1"
    )
    client.post("/login", data={"access_token": TOKEN})

    decision = client.get(f"/decision/{decision_id}")
    trace = client.get(f"/trace/{trace_id}")

    assert decision.status_code == 200
    assert "Inventory is low." in decision.text
    assert trace.status_code == 200
    assert "recommendation model" in trace.text


def test_missing_decision_and_trace_render_generic_not_found(tmp_path: Path) -> None:
    client = TestClient(app_for(Clock(), tmp_path), base_url="http://127.0.0.1")
    client.post("/login", data={"access_token": TOKEN})

    assert client.get("/decision/not-a-real-decision").status_code == 404
    assert client.get("/trace/not-a-real-trace").status_code == 404


def test_feedback_form_enforces_request_guards_and_uses_prg(tmp_path: Path) -> None:
    database_path, decision_id, _ = seeded_database(tmp_path)
    client = TestClient(
        app_for(Clock(), tmp_path, database_path), base_url="http://127.0.0.1:8787"
    )
    client.post("/login", data={"access_token": TOKEN})
    card = client.get(f"/decision/{decision_id}")
    csrf_token = re.search(r'name="csrf_token" value="([^"]+)"', card.text)
    idempotency_key = re.search(r'name="idempotency_key" value="([^"]+)"', card.text)
    assert csrf_token is not None
    assert idempotency_key is not None
    payload = {
        "csrf_token": csrf_token.group(1),
        "idempotency_key": idempotency_key.group(1),
        "verdict": "agree",
        "free_text": "<script>not executable</script>",
        "corrected_recommendation": '{"action":"order"}',
    }
    url = f"/decision/{decision_id}/feedback"

    assert client.post(url, data=payload, headers={"host": "wrong"}).status_code == 400
    assert client.post(
        url,
        data=payload | {"csrf_token": "wrong"},
        headers={"host": "127.0.0.1:8787"},
    ).status_code == 400
    assert client.post(
        url,
        data=payload,
        headers={"host": "127.0.0.1:8787", "origin": "http://evil.example"},
    ).status_code == 400

    response = client.post(url, data=payload, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/decision/{decision_id}"
    card = client.get(response.headers["location"])
    assert "agree" in card.text
    assert "&lt;script&gt;not executable&lt;/script&gt;" in card.text
    assert "<script>not executable</script>" not in card.text


def test_override_form_writes_an_operational_action_with_prg(tmp_path: Path) -> None:
    database_path, decision_id, _ = seeded_database(tmp_path)
    client = TestClient(
        app_for(Clock(), tmp_path, database_path), base_url="http://127.0.0.1:8787"
    )
    client.post("/login", data={"access_token": TOKEN})
    card = client.get(f"/decision/{decision_id}")
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', card.text)
    key = re.search(r'name="idempotency_key" value="([^"]+)"', card.text)
    assert csrf is not None
    assert key is not None

    response = client.post(
        f"/decision/{decision_id}/override",
        data={
            "csrf_token": csrf.group(1),
            "idempotency_key": key.group(1),
            "action": "modified",
            "modified_value": '{"action":"hold"}',
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    database = Database.open_read_only(database_path)
    try:
        detail = Repository(database).decision_detail(decision_id)
    finally:
        database.close()
    assert detail is not None
    assert detail.overrides[0].action == "modified"
    assert detail.overrides[0].actor == "local-planner"


@pytest.mark.parametrize(
    "query",
    ["?sort=bogus", "?from=not-a-date", "?confidence=bogus", "?cursor=not-valid-base64!!"],
)
def test_queue_rejects_malformed_filters_without_a_server_error(tmp_path: Path, query: str) -> None:
    client = TestClient(app_for(Clock(), tmp_path), base_url="http://127.0.0.1")
    client.post("/login", data={"access_token": TOKEN})

    response = client.get(f"/{query}", follow_redirects=False)

    assert response.status_code == 400
    assert "Traceback" not in response.text
