"""Resolve explicitly authorized Discord settings without ambient mutation."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping
from urllib.parse import urlsplit


@dataclass(frozen=True)
class DiscordConfig:
    status: Literal["ready", "disabled", "invalid"]
    webhook_url: str | None
    report_url: str | None
    reason: Literal["missing_url", "egress_not_authorized", "invalid_url"] | None


def _loopback_report_url(value: str | None) -> str | None:
    if not value or any(character.isspace() or ord(character) < 32 for character in value):
        return None
    if "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port == 0
        ):
            return None
        if host == "localhost" or ipaddress.ip_address(host).is_loopback:
            return value
    except ValueError:
        pass
    return None


def load_discord_config(project_root: Path, environ: Mapping[str, str]) -> DiscordConfig:
    file_values = _discord_env_values(Path(project_root) / ".env")

    def resolve(key: str) -> str | None:
        for value in (environ.get(key), file_values.get(key)):
            if value and value.strip():
                return value
        return None

    url = resolve("GLASSBOX_DISCORD_WEBHOOK_URL")
    if url is None:
        return DiscordConfig("disabled", None, None, "missing_url")
    if resolve("GLASSBOX_DISCORD_ALLOW_EGRESS") != "1":
        return DiscordConfig("disabled", None, None, "egress_not_authorized")
    if not re.fullmatch(r"https://discord\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]+", url):
        return DiscordConfig("invalid", None, None, "invalid_url")
    return DiscordConfig(
        "ready",
        url,
        _loopback_report_url(resolve("GLASSBOX_DISCORD_REPORT_URL")),
        None,
    )


def _discord_env_values(path: Path) -> dict[str, str]:
    """Read only Discord's single-line settings, without expansion or mutation."""
    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    values = {}
    for line in contents.splitlines():
        match = re.fullmatch(
            r"\s*(?:export\s+)?(GLASSBOX_DISCORD_[A-Z_]+)\s*=\s*(.*)", line
        )
        if match is None:
            continue
        key, value = match.groups()
        if value.startswith(("'", '"')):
            quoted = re.fullmatch(r"(['\"])(.*?)\1\s*(?:#.*)?", value)
            if quoted is None:
                continue
            value = quoted.group(2)
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[key] = value
    return values
