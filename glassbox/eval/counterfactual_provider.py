"""Counterfactual transport delegates to the existing lazy provider adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .counterfactual_config import CounterfactualConfig
from .judge_config import JudgeConfig
from .judge_provider import JudgeProvider, JudgeProviderTimeout, create_judge_provider
from .judge_provider import validate_request_timeout as validate_request_timeout


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
        try:
            if timeout_seconds is None:
                return self.transport.judge(system_prompt, user_prompt)
            return self.transport.judge(system_prompt, user_prompt, timeout_seconds=timeout_seconds)
        except JudgeProviderTimeout as exc:
            if timeout_seconds is None:
                raise
            raise CounterfactualProviderTimeout("counterfactual request timed out") from exc


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
