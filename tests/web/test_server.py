from __future__ import annotations

from datetime import timedelta

import pytest

from glassbox.web.server import load_server_config, run_server

TOKEN = "t" * 32


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
