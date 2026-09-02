"""Runtime configuration and fail-open collector facade for the SDK."""

from __future__ import annotations

import atexit
import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .redaction import RedactionHook, Redactor

_LOGGER = logging.getLogger("glassbox.sdk")

_ALLOWED_ENVIRONMENTS = frozenset({"dev", "shadow", "prod"})


class EventSink(Protocol):
    """The collector operations the SDK needs, independent of its implementation."""

    def emit(self, event: object) -> bool:
        """Attempt to accept one canonical event."""

    def flush(self, timeout: float | None = None) -> bool:
        """Wait for accepted events."""

    def shutdown(self, timeout: float | None = None) -> bool:
        """Stop accepting and drain events."""


class BlobSink(Protocol):
    """Content-addressed storage used by optional SDK content capture."""

    def put(self, content: bytes) -> str:
        """Persist content and return its stable reference."""


@dataclass(frozen=True)
class SDKConfig:
    """The active SDK configuration."""

    agent: str = ""
    version: str = ""
    environment: str = "dev"
    enabled: bool = False
    collector: EventSink | None = None
    blob_store: BlobSink | None = None
    redactor: Redactor = Redactor()


_config = SDKConfig()
_atexit_registered = False


def init(
    *,
    agent: str,
    version: str,
    env: str = "dev",
    collector: EventSink | None = None,
    blob_store: BlobSink | None = None,
    redaction_hooks: Iterable[RedactionHook] = (),
) -> None:
    """Configure tracing without allowing setup failures into agent code."""
    global _atexit_registered, _config
    try:
        if env not in _ALLOWED_ENVIRONMENTS:
            raise ValueError(f"env must be one of {sorted(_ALLOWED_ENVIRONMENTS)}, got {env!r}")
        _config = SDKConfig(
            agent=agent,
            version=version,
            environment=env,
            enabled=os.environ.get("GLASSBOX_ENABLED", "1") != "0",
            collector=collector,
            blob_store=blob_store,
            redactor=Redactor(redaction_hooks),
        )
        if not _atexit_registered:
            atexit.register(_shutdown_at_exit)
            _atexit_registered = True
    except Exception:
        _config = SDKConfig()
        _LOGGER.warning("Glassbox initialization failed; tracing is disabled", exc_info=True)


def get_config() -> SDKConfig:
    """Return the active configuration."""
    return _config


def emit(event: object) -> bool:
    """Submit an event through the injected collector without raising."""
    config = get_config()
    if not config.enabled or config.collector is None:
        return False
    try:
        return config.collector.emit(event)
    except Exception:
        _LOGGER.warning("Glassbox collector rejected telemetry", exc_info=True)
        return False


def flush(timeout: float | None = None) -> bool:
    """Flush the injected collector, returning failure rather than raising."""
    config = get_config()
    if not config.enabled or config.collector is None:
        return True
    try:
        return config.collector.flush(timeout)
    except Exception:
        _LOGGER.warning("Glassbox collector flush failed", exc_info=True)
        return False


def shutdown(timeout: float | None = None) -> bool:
    """Shut down the injected collector, returning failure rather than raising."""
    config = get_config()
    if not config.enabled or config.collector is None:
        return True
    try:
        return config.collector.shutdown(timeout)
    except Exception:
        _LOGGER.warning("Glassbox collector shutdown failed", exc_info=True)
        return False


def redact(value: Any) -> Any:
    """Redact every event payload leaf before it reaches the collector."""
    try:
        if isinstance(value, Mapping):
            return {key: redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, tuple):
            return tuple(redact(item) for item in value)
        return get_config().redactor.apply(value)
    except Exception:
        # A broken redactor must not make the host workload fail. Callers drop
        # the affected telemetry rather than persisting an unredacted value.
        raise ValueError("Glassbox redaction failed") from None


def capture(content: str) -> str | None:
    """Redact and persist optional content, never exposing failures to an agent."""
    config = get_config()
    if not config.enabled or config.blob_store is None:
        return None
    try:
        return config.blob_store.put(redact(content).encode("utf-8"))
    except Exception:
        _LOGGER.warning("Glassbox blob capture failed", exc_info=True)
        return None


def reset_for_testing() -> None:
    """Restore the inert default configuration for isolated SDK tests."""
    global _config
    _config = SDKConfig()
    from .tracer import reset_warning_for_testing

    reset_warning_for_testing()


def _shutdown_at_exit() -> None:
    shutdown(timeout=1.0)


__all__ = [
    "BlobSink",
    "EventSink",
    "SDKConfig",
    "capture",
    "emit",
    "flush",
    "get_config",
    "init",
    "redact",
    "shutdown",
]
