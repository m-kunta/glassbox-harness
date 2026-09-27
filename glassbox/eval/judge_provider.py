"""Lazy, provider-neutral LLM judge adapters and strict response parsing.

``JudgeProvider`` is a thin network-adapter boundary: ``judge()`` sends a
system/user prompt pair to the configured model and returns whatever raw text
came back, with no parsing or validation of its own. Response validation is a
separate, pure concern handled by :func:`parse_judge_response`, which callers
invoke on the raw string *after* getting it back from ``judge()``. Keeping
these separate means the adapter boundary stays a simple "talk to the
provider" shim, while strict-JSON acceptance/rejection rules live in one
place that has nothing to do with any particular provider's SDK.

Each optional provider SDK (``anthropic``, ``openai``, ``google.genai``) is
imported lazily, inside its own factory function only -- never at module
import time -- so a fresh checkout with none of those extras installed can
still import this module and construct an Ollama provider (which needs only
the standard library) or call :func:`parse_judge_response`.
"""

from __future__ import annotations

import json
import math
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from .judge_config import JudgeConfig

_OLLAMA_REQUEST_TIMEOUT_SECONDS = 120
_JUDGE_TEMPERATURE = 0
_MIN_SCORE = 1
_MAX_SCORE = 5


class JudgeProvider(Protocol):
    """A configured adapter that can put one system/user prompt to a judge model."""

    def judge(self, system_prompt: str, user_prompt: str) -> str:
        """Return the model's raw text response; callers parse it themselves."""
        ...


@dataclass(frozen=True)
class JudgeParseResult:
    """The outcome of validating one raw judge response string.

    Exactly one of two shapes holds: a successful parse carries a non-null
    ``score`` and ``rationale`` with ``error`` unset, or a failed parse
    carries a non-empty ``error`` with ``score`` and ``rationale`` unset.
    """

    score: int | None
    rationale: str | None
    error: str | None


def create_judge_provider(config: JudgeConfig) -> JudgeProvider:
    """Build the adapter for ``config.provider``, importing its SDK lazily."""
    factory = _FACTORIES.get(config.provider)
    if factory is None:
        raise ValueError(f"unsupported judge provider {config.provider!r}")
    return factory(config)


def parse_judge_response(raw: str) -> JudgeParseResult:
    """Strictly validate one raw judge response string.

    Requires the response to be a JSON object with exactly a finite integer
    ``score`` from 1 through 5 and a non-empty string ``rationale``. This
    rejects malformed JSON, non-object top-level values, non-integer or
    out-of-range scores (including JSON's non-standard ``NaN``/``Infinity``
    extensions, which this parser refuses even though the standard library's
    ``json.loads`` accepts them by default), and missing or blank rationales,
    returning a typed error instead of raising.
    """
    try:
        payload = json.loads(raw, parse_constant=_reject_non_finite_constant)
    except (ValueError, TypeError) as exc:
        return JudgeParseResult(score=None, rationale=None, error=f"invalid JSON response: {exc}")

    if not isinstance(payload, dict):
        return JudgeParseResult(
            score=None, rationale=None, error="judge response JSON must be an object"
        )

    score = payload.get("score")
    if isinstance(score, bool) or not isinstance(score, int) or not math.isfinite(score):
        return JudgeParseResult(
            score=None, rationale=None, error="judge response score must be a finite integer"
        )
    if not (_MIN_SCORE <= score <= _MAX_SCORE):
        return JudgeParseResult(
            score=None,
            rationale=None,
            error=f"judge response score must be between {_MIN_SCORE} and {_MAX_SCORE}",
        )

    rationale = payload.get("rationale")
    if not isinstance(rationale, str) or not rationale.strip():
        return JudgeParseResult(
            score=None, rationale=None, error="judge response rationale must be a non-empty string"
        )

    return JudgeParseResult(score=score, rationale=rationale, error=None)


def _reject_non_finite_constant(token: str) -> float:
    raise ValueError(f"non-finite JSON constant {token!r} is not accepted")


def _create_claude_provider(config: JudgeConfig) -> JudgeProvider:
    from anthropic import Anthropic

    client = Anthropic(api_key=config.credential, base_url=config.base_url)
    return _ClaudeJudgeProvider(client=client, model=config.model)


@dataclass(frozen=True)
class _ClaudeJudgeProvider:
    client: Any
    model: str

    def judge(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            temperature=_JUDGE_TEMPERATURE,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return str(response.content[0].text)


def _create_openai_provider(config: JudgeConfig) -> JudgeProvider:
    from openai import OpenAI

    client = OpenAI(api_key=config.credential, base_url=config.base_url)
    return _OpenAIJudgeProvider(client=client, model=config.model)


@dataclass(frozen=True)
class _OpenAIJudgeProvider:
    client: Any
    model: str

    def judge(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            temperature=_JUDGE_TEMPERATURE,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return str(response.choices[0].message.content)


def _create_gemini_provider(config: JudgeConfig) -> JudgeProvider:
    from google import genai

    client = genai.Client(
        api_key=config.credential, http_options={"base_url": config.base_url}
    )
    return _GeminiJudgeProvider(client=client, model=config.model)


@dataclass(frozen=True)
class _GeminiJudgeProvider:
    client: Any
    model: str

    def judge(self, system_prompt: str, user_prompt: str) -> str:
        response = self.client.models.generate_content(
            model=self.model,
            contents=user_prompt,
            config={
                "system_instruction": system_prompt,
                "temperature": _JUDGE_TEMPERATURE,
            },
        )
        return str(response.text)


def _create_ollama_provider(config: JudgeConfig) -> JudgeProvider:
    return _OllamaJudgeProvider(base_url=config.base_url, model=config.model)


@dataclass(frozen=True)
class _OllamaJudgeProvider:
    """Standard-library HTTP adapter -- needs no runtime HTTP extra installed."""

    base_url: str
    model: str

    def judge(self, system_prompt: str, user_prompt: str) -> str:
        payload = json.dumps(
            {
                "model": self.model,
                "system": system_prompt,
                "prompt": user_prompt,
                "stream": False,
                "options": {"temperature": _JUDGE_TEMPERATURE},
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(
            request, timeout=_OLLAMA_REQUEST_TIMEOUT_SECONDS
        ) as response:
            body = json.loads(response.read().decode("utf-8"))
        return str(body["response"])


_FACTORIES = {
    "claude": _create_claude_provider,
    "openai": _create_openai_provider,
    "gemini": _create_gemini_provider,
    "ollama": _create_ollama_provider,
}
