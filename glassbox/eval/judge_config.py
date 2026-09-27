"""Scoped configuration loading for the LLM judge.

Configuration is read from exactly two places: the *project root's own*
``.env`` file (never any other directory's, such as an agent's working
directory) and the process environment, which takes precedence when a value
is present and non-empty there. Only the ``GLASSBOX_JUDGE_*`` keys this
module knows about, plus the credential key for whichever provider is
selected, are ever looked up -- an unselected provider's credential key is
never read, dotenv- or process-side.

Remote providers (Claude, OpenAI, Gemini) always talk to a fixed HTTPS root;
operators cannot override it. Ollama is the only provider whose base URL is
configurable, via ``GLASSBOX_JUDGE_OLLAMA_URL``, and whether that URL is
loopback (local) or not (remote, requiring the caller's own egress
confirmation) is classified by parsing the URL text alone -- this module
never performs a network request or follows a redirect to do that
classification.

Provider credentials use each provider's own conventional environment
variable name (``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``, ``GEMINI_API_KEY``)
rather than a ``GLASSBOX_JUDGE_``-prefixed alias, so operators can reuse a
credential they may already have configured for that provider elsewhere.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from dotenv import dotenv_values

_PROVIDER_KEY = "GLASSBOX_JUDGE_PROVIDER"
_MODEL_KEY = "GLASSBOX_JUDGE_MODEL"
_OLLAMA_URL_KEY = "GLASSBOX_JUDGE_OLLAMA_URL"

_REMOTE_BASE_URLS: Mapping[str, str] = {
    "claude": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com",
}

_CREDENTIAL_KEYS: Mapping[str, str] = {
    "claude": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

_SUPPORTED_PROVIDERS = ("claude", "openai", "gemini", "ollama")


class JudgeConfigError(RuntimeError):
    """Raised when the judge cannot be configured from the scoped environment."""


@dataclass(frozen=True)
class JudgeConfig:
    """One fully resolved judge provider configuration."""

    provider: str
    model: str
    credential: str | None
    base_url: str
    is_remote: bool


def load_judge_config(project_root: Path, environ: Mapping[str, str]) -> JudgeConfig:
    """Load and validate judge configuration for one invocation.

    ``project_root``'s own ``.env`` file (if any) is read with
    ``dotenv_values``, which never mutates ``os.environ``. ``environ`` is
    consulted for the same keys and wins whenever it carries a non-empty
    value; neither ``environ`` nor the parsed dotenv mapping is mutated by
    this function.
    """
    file_values = dotenv_values(Path(project_root) / ".env")

    def resolve(key: str) -> str | None:
        process_value = environ.get(key)
        if process_value:
            return process_value
        file_value = file_values.get(key)
        return file_value if file_value else None

    provider = resolve(_PROVIDER_KEY)
    if not provider:
        raise JudgeConfigError(f"{_PROVIDER_KEY} is required")
    if provider not in _SUPPORTED_PROVIDERS:
        raise JudgeConfigError(
            f"unsupported judge provider {provider!r}; expected one of "
            f"{', '.join(_SUPPORTED_PROVIDERS)}"
        )

    model = resolve(_MODEL_KEY)
    if not model:
        raise JudgeConfigError(f"{_MODEL_KEY} is required")

    if provider == "ollama":
        base_url = resolve(_OLLAMA_URL_KEY)
        if not base_url:
            raise JudgeConfigError(f"{_OLLAMA_URL_KEY} is required for provider 'ollama'")
        return JudgeConfig(
            provider=provider,
            model=model,
            credential=None,
            base_url=base_url,
            is_remote=not _is_loopback_url(base_url),
        )

    credential_key = _CREDENTIAL_KEYS[provider]
    credential = resolve(credential_key)
    if not credential:
        raise JudgeConfigError(f"{credential_key} is required for provider {provider!r}")

    return JudgeConfig(
        provider=provider,
        model=model,
        credential=credential,
        base_url=_REMOTE_BASE_URLS[provider],
        is_remote=True,
    )


def _is_loopback_url(url: str) -> bool:
    """Classify a URL's destination as loopback purely from its literal text.

    No DNS resolution, connection, or redirect is followed -- egress
    classification must not depend on what a request to the URL happens to
    do at call time.
    """
    hostname = urlparse(url).hostname
    if not hostname:
        raise JudgeConfigError(f"{_OLLAMA_URL_KEY} must be a URL with a host, got {url!r}")
    if hostname == "localhost":
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return address.is_loopback
