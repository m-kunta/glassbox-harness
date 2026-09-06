"""Validated startup for Glassbox's local web server."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import uvicorn
from fastapi import FastAPI

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})
_MIN_TOKEN_LENGTH = 32


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class ServerConfig:
    """Validated configuration for the loopback-only web server."""

    host: str
    port: int
    access_token: str
    session_idle_timeout: timedelta = timedelta(minutes=30)


def load_server_config(
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    environ: Mapping[str, str] | None = None,
    session_idle_timeout: timedelta = timedelta(minutes=30),
) -> ServerConfig:
    """Read and validate local-server configuration before any socket bind."""
    source = os.environ if environ is None else environ
    token = source.get("GLASSBOX_LOCAL_ACCESS_TOKEN", "")
    if host not in _LOOPBACK_HOSTS:
        raise ValueError("host must be exactly 127.0.0.1 or ::1 (loopback only)")
    if not 1 <= port <= 65_535:
        raise ValueError("port must be between 1 and 65535")
    if len(token) < _MIN_TOKEN_LENGTH:
        raise ValueError("GLASSBOX_LOCAL_ACCESS_TOKEN must contain at least 32 characters")
    if session_idle_timeout <= timedelta():
        raise ValueError("session_idle_timeout must be positive")
    return ServerConfig(host, port, token, session_idle_timeout)


def create_app(config: ServerConfig, *, clock: Callable[[], datetime] = utc_now) -> FastAPI:
    """Create the P2.1 application shell; routes arrive with authentication."""
    del config, clock
    return FastAPI()


def run_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Start the validated, local Uvicorn server."""
    config = load_server_config(host=host, port=port, environ=environ)
    uvicorn.run(create_app(config), host=config.host, port=config.port)
