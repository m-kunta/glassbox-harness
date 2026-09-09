"""Validated startup for Glassbox's local web server."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import RequestResponseEndpoint

from glassbox.store import Database, ReadOnlyDatabaseError, Repository
from glassbox.store.repository import FeedbackSubmission

from .auth import SESSION_COOKIE_NAME, SessionStore, token_matches
from .read_service import QueueRequest, ReadService

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1"})
_MIN_TOKEN_LENGTH = 32
_MAX_VERDICT_LENGTH = 16
_MAX_REASON_CODE_LENGTH = 64
_MAX_FREE_TEXT_LENGTH = 4 * 1024
_MAX_CORRECTED_RECOMMENDATION_LENGTH = 16 * 1024


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
    database_path: Path = Path("glassbox.sqlite3")


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
    return ServerConfig(
        host,
        port,
        token,
        session_idle_timeout,
        Path(source.get("GLASSBOX_DATABASE", "glassbox.sqlite3")),
    )


def create_app(config: ServerConfig, *, clock: Callable[[], datetime] = utc_now) -> FastAPI:
    """Create the authenticated read-only planner application."""
    database = Database.open_read_only(config.database_path)
    database.close()
    templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))
    sessions = SessionStore(config.session_idle_timeout, clock=clock)
    app = FastAPI()
    app.state.sessions = sessions
    app.mount(
        "/static", StaticFiles(directory=str(Path(__file__).with_name("static"))), name="static"
    )

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

    def service() -> ReadService:
        return ReadService(config.database_path)

    def unavailable(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"title": "Planner unavailable", "message": "The planner data is unavailable."},
            status_code=503,
        )

    def invalid_request(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"title": "Invalid request", "message": "One or more queue filters are invalid."},
            status_code=400,
        )

    def feedback_error(request: Request, status_code: int = 400) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"title": "Feedback not saved", "message": "The feedback request could not be saved."},
            status_code=status_code,
        )

    @app.get("/")
    def root(request: Request) -> Response:
        try:
            page = service().queue(
                QueueRequest(
                    agent_name=request.query_params.get("agent"),
                    decision_type=request.query_params.get("decision_type"),
                    date_from=request.query_params.get("from"),
                    date_to=request.query_params.get("to"),
                    confidence=request.query_params.get("confidence"),
                    override_status=request.query_params.get("override_status"),
                    sort=request.query_params.get("sort", "timestamp"),
                    cursor=request.query_params.get("cursor"),
                )
            )
        except ValueError:
            return invalid_request(request)
        except (ReadOnlyDatabaseError, sqlite3.Error):
            return unavailable(request)
        return templates.TemplateResponse(request, "queue.html", {"page": page})

    @app.get("/decision/{decision_id}")
    def decision_card(request: Request, decision_id: str) -> Response:
        try:
            card = service().decision_card(decision_id)
        except (ReadOnlyDatabaseError, sqlite3.Error):
            return unavailable(request)
        if card is None:
            return templates.TemplateResponse(
                request,
                "error.html",
                {"title": "Not found", "message": "The requested decision was not found."},
                status_code=404,
            )
        return templates.TemplateResponse(
            request,
            "decision_card.html",
            {
                "card": card,
                "csrf_token": request.state.csrf_token,
                "idempotency_key": secrets.token_urlsafe(32),
            },
        )

    @app.post("/decision/{decision_id}/feedback")
    def feedback(
        request: Request,
        decision_id: str,
        csrf_token: Annotated[str, Form()],
        idempotency_key: Annotated[str, Form()],
        verdict: Annotated[str, Form()],
        reason_code: Annotated[str | None, Form()] = None,
        free_text: Annotated[str | None, Form()] = None,
        corrected_recommendation: Annotated[str | None, Form()] = None,
    ) -> Response:
        if not _valid_feedback_origin(request, config):
            return feedback_error(request)
        if not token_matches(csrf_token, request.state.csrf_token):
            return feedback_error(request)
        try:
            submission = _feedback_submission(
                decision_id,
                verdict,
                reason_code,
                free_text,
                corrected_recommendation,
                idempotency_key,
                clock(),
            )
        except ValueError:
            return feedback_error(request)
        database: Database | None = None
        try:
            database = Database.open(config.database_path)
            repository = Repository(database)
            if repository.decision_detail(decision_id) is None:
                return templates.TemplateResponse(
                    request,
                    "error.html",
                    {"title": "Not found", "message": "The requested decision was not found."},
                    status_code=404,
                )
            repository.record_feedback(submission)
        except sqlite3.OperationalError:
            return templates.TemplateResponse(
                request,
                "error.html",
                {"title": "Feedback not saved", "message": "Please retry the feedback request."},
                status_code=503,
            )
        except (ValueError, sqlite3.IntegrityError):
            return feedback_error(request)
        finally:
            if database is not None:
                database.close()
        return RedirectResponse(f"/decision/{decision_id}", status_code=303)

    @app.get("/trace/{trace_id}")
    def trace(request: Request, trace_id: str) -> Response:
        try:
            view = service().trace(trace_id)
        except (ReadOnlyDatabaseError, sqlite3.Error):
            return unavailable(request)
        if view is None:
            return templates.TemplateResponse(
                request,
                "error.html",
                {"title": "Not found", "message": "The requested trace was not found."},
                status_code=404,
            )
        return templates.TemplateResponse(request, "trace.html", {"view": view})

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


def _valid_feedback_origin(request: Request, config: ServerConfig) -> bool:
    host = _configured_host(config)
    if request.headers.get("host") != host:
        return False
    origin = request.headers.get("origin")
    return origin is None or origin == f"http://{host}"


def _configured_host(config: ServerConfig) -> str:
    host = f"[{config.host}]" if config.host == "::1" else config.host
    return f"{host}:{config.port}"


def _feedback_submission(
    decision_id: str,
    verdict: str,
    reason_code: str | None,
    free_text: str | None,
    corrected_recommendation: str | None,
    idempotency_key: str,
    created_at: datetime,
) -> FeedbackSubmission:
    if (
        len(verdict) > _MAX_VERDICT_LENGTH
        or verdict not in {"agree", "disagree", "uncertain"}
        or not idempotency_key
        or (reason_code is not None and len(reason_code) > _MAX_REASON_CODE_LENGTH)
        or (free_text is not None and len(free_text) > _MAX_FREE_TEXT_LENGTH)
        or (
            corrected_recommendation is not None
            and len(corrected_recommendation) > _MAX_CORRECTED_RECOMMENDATION_LENGTH
        )
    ):
        raise ValueError("invalid feedback form")
    parsed_recommendation: object | None = None
    if corrected_recommendation:
        try:
            parsed_recommendation = json.loads(corrected_recommendation)
        except json.JSONDecodeError as error:
            raise ValueError("invalid corrected recommendation") from error
    value = (int(created_at.timestamp() * 1_000) << 80) | secrets.randbits(80)
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    feedback_id = "".join(alphabet[(value >> (5 * index)) & 31] for index in range(25, -1, -1))
    return FeedbackSubmission(
        feedback_id,
        decision_id,
        verdict,  # type: ignore[arg-type]
        reason_code or None,
        free_text or None,
        parsed_recommendation,
        created_at,
        idempotency_key,
    )
