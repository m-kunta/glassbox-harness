"""Validated startup for Glassbox's local web server."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import RequestResponseEndpoint

from .auth import SESSION_COOKIE_NAME, SessionStore, token_matches

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
    """Create the authenticated P2.1 application shell."""
    templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))
    sessions = SessionStore(config.session_idle_timeout, clock=clock)
    app = FastAPI()
    app.state.sessions = sessions

    @app.middleware("http")
    async def require_session(request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path in {"/login", "/health"}:
            return await call_next(request)
        session_id = request.cookies.get(SESSION_COOKIE_NAME, "")
        session = sessions.get(session_id)
        if session is None:
            return RedirectResponse("/login", status_code=303)
        request.state.csrf_token = session.csrf_token
        sessions.touch(session_id)
        response = await call_next(request)
        return response

    @app.get("/login")
    def login_form(request: Request) -> Response:
        return templates.TemplateResponse(request, "login.html", {"error": False})

    @app.post("/login")
    def login(request: Request, access_token: Annotated[str, Form()]) -> Response:
        if not token_matches(access_token, config.access_token):
            return templates.TemplateResponse(
                request, "login.html", {"error": True}, status_code=401
            )
        session = sessions.create()
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE_NAME,
            session.session_id,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return response

    @app.get("/health")
    def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/")
    def root() -> PlainTextResponse:
        return PlainTextResponse("Planner views are not available yet.", status_code=503)

    return app


def run_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Start the validated, local Uvicorn server."""
    config = load_server_config(host=host, port=port, environ=environ)
    uvicorn.run(create_app(config), host=config.host, port=config.port)
