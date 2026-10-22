"""Counterfactual transport delegates to the existing lazy provider adapters."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Protocol

from .counterfactual_config import CounterfactualConfig
from .judge_config import JudgeConfig
from .judge_provider import JudgeProvider, create_judge_provider
from .judge_provider import validate_request_timeout as validate_request_timeout

# A timed-out call retains admission until its underlying transport exits.
# The slot spans all providers, endpoints, and counterfactual provider instances.
_BOUNDED_REQUEST_SLOT = threading.BoundedSemaphore(1)


class CounterfactualProviderTimeout(TimeoutError):
    """The explicitly bounded counterfactual provider request timed out."""


class CounterfactualProvider(Protocol):
    def complete(
        self, system_prompt: str, user_prompt: str, *, timeout_seconds: float | None = None,
    ) -> str:
        """Return raw provider text for separate, strict response validation."""
        ...


@dataclass(frozen=True)
class _CounterfactualTransport:
    transport: JudgeProvider

    def complete(
        self, system_prompt: str, user_prompt: str, *, timeout_seconds: float | None = None,
    ) -> str:
        validate_request_timeout(timeout_seconds)
        if timeout_seconds is None:
            return self.transport.judge(system_prompt, user_prompt)
        try:
            return _bounded_completion(
                self.transport, system_prompt, user_prompt, timeout_seconds,
            )
        except TimeoutError as exc:
            raise CounterfactualProviderTimeout("counterfactual request timed out") from exc


def _bounded_completion(
    transport: JudgeProvider, system_prompt: str, user_prompt: str, timeout_seconds: float,
) -> str:
    """Bound caller elapsed time while containing any unfinished transport call."""
    deadline = time.monotonic() + timeout_seconds
    result: Future[str] = Future()
    if not _BOUNDED_REQUEST_SLOT.acquire(blocking=False):
        raise TimeoutError("a bounded counterfactual request is still active")

    def complete() -> None:
        try:
            response = transport.judge(
                system_prompt, user_prompt, timeout_seconds=timeout_seconds,
            )
        except BaseException as exc:
            _BOUNDED_REQUEST_SLOT.release()
            result.set_exception(exc)
        else:
            _BOUNDED_REQUEST_SLOT.release()
            result.set_result(response)

    try:
        worker = threading.Thread(
            target=complete, name="glassbox-counterfactual-request", daemon=True,
        )
        worker.start()
    except BaseException:
        _BOUNDED_REQUEST_SLOT.release()
        raise
    response = result.result(timeout=max(0.0, deadline - time.monotonic()))
    if time.monotonic() >= deadline:
        raise TimeoutError("counterfactual request exceeded its elapsed deadline")
    return response


def create_counterfactual_provider(
    config: CounterfactualConfig, *,
    transport_factory: Callable[[JudgeConfig], JudgeProvider] | None = None,
) -> CounterfactualProvider:
    """Select a lazy adapter using resolved, independent configuration."""
    transport = (transport_factory or create_judge_provider)(JudgeConfig(
        provider=config.provider, model=config.model, credential=config.credential,
        base_url=config.base_url, is_remote=config.is_remote,
    ))
    return _CounterfactualTransport(transport)
