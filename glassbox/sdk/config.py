"""Runtime configuration and fail-open collector facade for the SDK."""

from __future__ import annotations

import atexit
import logging
import os
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
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
    capture_executor: ThreadPoolExecutor | None = None
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
            capture_executor=ThreadPoolExecutor(max_workers=1, thread_name_prefix="glassbox-blobs")
            if blob_store is not None
            else None,
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
    if not config.enabled:
        return True
    try:
        if config.capture_executor is not None:
            config.capture_executor.submit(lambda: None).result(timeout=timeout)
        return config.collector is None or config.collector.flush(timeout)
    except Exception:
        _LOGGER.warning("Glassbox collector flush failed", exc_info=True)
        return False


def shutdown(timeout: float | None = None) -> bool:
    """Shut down the injected collector, returning failure rather than raising."""
    config = get_config()
    if not config.enabled:
        return True
    try:
        captured = flush(timeout)
        if config.capture_executor is not None:
            config.capture_executor.shutdown(wait=False, cancel_futures=False)
        return captured and (config.collector is None or config.collector.shutdown(timeout))
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


def capture(content: str) -> Future[str | None] | None:
    """Queue redacted content persistence without blocking the agent thread."""
    config = get_config()
    if not config.enabled or config.blob_store is None or config.capture_executor is None:
        return None
    try:
        redacted = redact(content).encode("utf-8")
        return config.capture_executor.submit(_put_blob, config.blob_store, redacted)
    except Exception:
        _LOGGER.warning("Glassbox blob capture failed", exc_info=True)
        return None


def reset_for_testing() -> None:
    """Restore the inert default configuration for isolated SDK tests."""
    global _config
    previous = _config
    _config = SDKConfig()
    if previous.capture_executor is not None:
        previous.capture_executor.shutdown(wait=True, cancel_futures=False)
    from .tracer import reset_warning_for_testing

    reset_warning_for_testing()


def _shutdown_at_exit() -> None:
    shutdown(timeout=1.0)


def _put_blob(blob_store: BlobSink, content: bytes) -> str | None:
    try:
        return blob_store.put(content)
    except Exception:
        _LOGGER.warning("Glassbox blob capture failed", exc_info=True)
        return None


def defer_capture(callback: Callable[..., None], *captures: Future[str | None] | None) -> None:
    """Run an event callback after its queued blob writes complete."""
    executor = get_config().capture_executor
    if executor is None:
        callback(*(_capture_result(capture) for capture in captures))
        return
    try:
        executor.submit(lambda: callback(*(_capture_result(capture) for capture in captures)))
    except Exception:
        _LOGGER.warning("Glassbox blob capture callback was dropped", exc_info=True)


def _capture_result(capture: Future[str | None] | None) -> str | None:
    try:
        return None if capture is None else capture.result()
    except Exception:
        return None


__all__ = [
    "BlobSink",
    "EventSink",
    "SDKConfig",
    "capture",
    "defer_capture",
    "emit",
    "flush",
    "get_config",
    "init",
    "redact",
    "shutdown",
]
