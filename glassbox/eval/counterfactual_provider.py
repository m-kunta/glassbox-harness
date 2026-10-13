"""Counterfactual transport delegates to the existing lazy provider adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .counterfactual_config import CounterfactualConfig
from .judge_config import JudgeConfig
from .judge_provider import JudgeProvider, create_judge_provider


class CounterfactualProvider(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Return raw provider text for separate, strict response validation."""
        ...


@dataclass(frozen=True)
class _CounterfactualTransport:
    transport: JudgeProvider

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        return self.transport.judge(system_prompt, user_prompt)


def create_counterfactual_provider(config: CounterfactualConfig) -> CounterfactualProvider:
    """Select a lazy adapter using resolved, independent configuration."""
    transport = create_judge_provider(JudgeConfig(
        provider=config.provider, model=config.model, credential=config.credential,
        base_url=config.base_url, is_remote=config.is_remote,
    ))
    return _CounterfactualTransport(transport)
