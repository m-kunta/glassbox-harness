"""Resolve counterfactual settings without changing the process environment."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from dotenv import dotenv_values

_PREFIX = "GLASSBOX_COUNTERFACTUAL_"
_CREDENTIAL_KEYS = {
    "claude": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}
_REMOTE_BASE_URLS = {
    "claude": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com",
}


class CounterfactualConfigError(RuntimeError):
    """The selected counterfactual provider could not be configured."""


@dataclass(frozen=True)
class CounterfactualConfig:
    provider: str
    model: str
    credential: str | None
    base_url: str
    is_remote: bool


def load_counterfactual_config(
    project_root: Path, environ: Mapping[str, str],
) -> CounterfactualConfig:
    """Read the root's dotenv and selected keys; non-empty process values win.

    Dotenv interpolation is disabled so file values cannot consult unrelated
    ambient variables. Provider SDK credentials and destinations are passed
    explicitly by the transport factory.
    """
    file_values = dotenv_values(Path(project_root) / ".env", interpolate=False)

    def resolve(key: str) -> str | None:
        value = environ.get(key) or file_values.get(key)
        return value if value and value.strip() else None

    provider = resolve(f"{_PREFIX}PROVIDER")
    if provider is None:
        raise CounterfactualConfigError(f"{_PREFIX}PROVIDER is required")
    if provider not in (*_CREDENTIAL_KEYS, "ollama"):
        raise CounterfactualConfigError("unsupported counterfactual provider")
    model = resolve(f"{_PREFIX}MODEL")
    if model is None:
        raise CounterfactualConfigError(f"{_PREFIX}MODEL is required")

    if provider == "ollama":
        base_url = resolve(f"{_PREFIX}OLLAMA_URL") or "http://127.0.0.1:11434"
        return CounterfactualConfig(provider, model, None, base_url, _is_remote_url(base_url))

    credential_key = _CREDENTIAL_KEYS[provider]
    credential = resolve(credential_key)
    if credential is None:
        raise CounterfactualConfigError(f"{credential_key} is required")
    return CounterfactualConfig(provider, model, credential, _REMOTE_BASE_URLS[provider], True)


def _is_remote_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        # Accessing port also validates malformed or out-of-range port values.
        port = parsed.port
        if (parsed.scheme not in {"http", "https"} or not host
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or port == 0):
            raise ValueError
    except ValueError:
        raise CounterfactualConfigError(f"{_PREFIX}OLLAMA_URL must be an HTTP(S) URL") from None
    if host == "localhost":
        return False
    try:
        return not ipaddress.ip_address(host).is_loopback
    except ValueError:
        return True
