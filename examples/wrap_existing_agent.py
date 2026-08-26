"""Configure local Glassbox persistence around an already-traced agent entry point.

Install this repository locally in the host agent environment first, for example:
``python -m pip install -e /path/to/glassbox``.  The representative
replenishment agent decorates ``TriageAgent.run`` with ``@trace``; this helper
only supplies its local collector lifecycle.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

import glassbox as gb
from glassbox.collector import Collector
from glassbox.store import Database, Repository

_T = TypeVar("_T")


def configure_tracing(database_path: Path) -> Collector:
    """Connect the public SDK to a local SQLite-backed collector."""
    collector = Collector(Repository(Database.open(database_path)))
    gb.init(agent="replenishment-triage", version="local", env="dev", collector=collector)
    return collector


def run_with_tracing(agent_run: Callable[..., _T], collector: Collector, /, *args: object) -> _T:
    """Run an existing agent callable and drain only its best-effort telemetry."""
    try:
        return agent_run(*args)
    finally:
        gb.flush(timeout=1.0)
        collector.shutdown(timeout=1.0)
