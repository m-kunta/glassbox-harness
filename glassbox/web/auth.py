"""Local-token authentication and in-memory session primitives."""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import RLock

SESSION_COOKIE_NAME = "glassbox_session"


@dataclass(frozen=True)
class Session:
    """An opaque local-browser session and its future CSRF secret."""

    session_id: str
    csrf_token: str
    last_activity: datetime


def token_matches(submitted: str, configured: str) -> bool:
    """Compare a submitted local token without short-circuiting on a mismatch."""
    return hmac.compare_digest(submitted, configured)


class SessionStore:
    """Thread-safe, process-local sessions that expire after inactivity."""

    def __init__(self, idle_timeout: timedelta, *, clock: Callable[[], datetime]) -> None:
        self._idle_timeout = idle_timeout
        self._clock = clock
        self._sessions: dict[str, Session] = {}
        self._lock = RLock()

    def create(self) -> Session:
        """Create and retain a new opaque session."""
        now = self._clock()
        session = Session(
            session_id=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
            last_activity=now,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        """Return an active session, removing it if it has expired."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if self._clock() - session.last_activity >= self._idle_timeout:
                del self._sessions[session_id]
                return None
            return session

    def touch(self, session_id: str) -> Session | None:
        """Refresh an existing active session's activity time."""
        with self._lock:
            session = self.get(session_id)
            if session is None:
                return None
            refreshed = Session(session.session_id, session.csrf_token, self._clock())
            self._sessions[session_id] = refreshed
            return refreshed
