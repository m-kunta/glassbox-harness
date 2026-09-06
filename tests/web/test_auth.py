from __future__ import annotations

from datetime import UTC, datetime, timedelta

from glassbox.web.auth import SessionStore, token_matches


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


def test_token_matches_only_the_configured_value() -> None:
    assert token_matches("a" * 32, "a" * 32) is True
    assert token_matches("b" * 32, "a" * 32) is False


def test_session_is_opaque_and_has_a_csrf_token() -> None:
    session = SessionStore(timedelta(minutes=30), clock=Clock()).create()

    assert session.session_id != session.csrf_token
    assert len(session.session_id) >= 32
    assert len(session.csrf_token) >= 32


def test_expired_session_is_removed_and_not_returned() -> None:
    clock = Clock()
    store = SessionStore(timedelta(minutes=30), clock=clock)
    session = store.create()
    clock.value += timedelta(minutes=30, seconds=1)

    assert store.get(session.session_id) is None
    assert store.get(session.session_id) is None


def test_touch_refreshes_activity_only_for_an_existing_session() -> None:
    clock = Clock()
    store = SessionStore(timedelta(minutes=30), clock=clock)
    session = store.create()
    clock.value += timedelta(minutes=29)

    refreshed = store.touch(session.session_id)

    assert refreshed is not None
    clock.value += timedelta(minutes=29)
    assert store.get(session.session_id) is not None
    assert store.touch("malformed") is None
