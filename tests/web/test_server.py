from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from glassbox.web.server import create_app, load_server_config, run_server

TOKEN = "t" * 32


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


def app_for(clock: Clock):
    config = load_server_config(
        environ={"GLASSBOX_LOCAL_ACCESS_TOKEN": TOKEN}, host="127.0.0.1", port=8787
    )
    return create_app(config, clock=clock)


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


def test_health_is_public_and_contains_no_decision_data() -> None:
    response = TestClient(app_for(Clock()), base_url="http://127.0.0.1").get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_failed_login_is_generic_and_does_not_set_a_cookie() -> None:
    response = TestClient(app_for(Clock()), base_url="http://127.0.0.1").post(
        "/login", data={"access_token": "wrong"}
    )

    assert response.status_code == 401
    assert "glassbox_session" not in response.headers.get("set-cookie", "")
    assert "wrong" not in response.text


def test_successful_login_sets_the_intended_cookie_attributes() -> None:
    client = TestClient(app_for(Clock()), base_url="http://127.0.0.1")

    response = client.post("/login", data={"access_token": TOKEN}, follow_redirects=False)

    cookie = response.headers["set-cookie"]
    assert response.status_code == 303
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie
    assert TOKEN not in cookie


def test_protected_route_redirects_without_session_and_refreshes_with_session() -> None:
    clock = Clock()
    client = TestClient(app_for(clock), base_url="http://127.0.0.1")

    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    client.post("/login", data={"access_token": TOKEN})
    clock.value += timedelta(minutes=29)
    assert client.get("/").status_code == 503
    clock.value += timedelta(minutes=29)
    assert client.get("/", follow_redirects=False).status_code == 503


def test_idle_session_is_rejected_after_its_timeout() -> None:
    clock = Clock()
    client = TestClient(app_for(clock), base_url="http://127.0.0.1")
    client.post("/login", data={"access_token": TOKEN})
    clock.value += timedelta(minutes=30, seconds=1)

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
