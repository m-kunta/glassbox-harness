"""Standalone, read-only HTML rendering for one Decision Card."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .read_models import DecisionCard

_ROOT = Path(__file__).parent
_TEMPLATES = Environment(
    loader=FileSystemLoader(_ROOT / "templates"), autoescape=select_autoescape(("html",))
)
_CSS = _ROOT / "static" / "glassbox.css"


class ExportError(RuntimeError):
    """A static export request cannot safely be completed."""


def render_decision_export(card: DecisionCard, live_base_url: str | None) -> str:
    """Return a self-contained, non-mutating HTML snapshot of *card*."""
    live_url = (
        None
        if live_base_url is None
        else _live_card_url(live_base_url, card.decision.event.decision_id)
    )
    return _TEMPLATES.get_template("decision_export.html").render(
        card=card, css=_CSS.read_text(encoding="utf-8"), live_url=live_url
    )


def write_decision_export(path: Path, html: str, *, overwrite: bool) -> None:
    """Write one export file, protecting an existing artifact by default."""
    if path.exists() and not overwrite:
        raise ExportError("output already exists; pass --overwrite to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def _live_card_url(base_url: str, decision_id: str) -> str:
    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or parsed.port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ExportError("live base URL must be a loopback HTTP origin")
    return f"{base_url.rstrip('/')}/decision/{decision_id}"
