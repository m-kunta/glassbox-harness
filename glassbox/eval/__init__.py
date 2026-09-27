"""Typed contracts and helpers for deterministic decision evaluation.

This package's own ``__init__`` deliberately imports only the dependency-free
deterministic-evaluation surface (``.models``, ``.target``). The judge
calibration modules -- ``judge_config``, ``judge_provider``, ``judge_models``,
and ``judge`` (this task's orchestration entry point, ``run_judge``) -- are
never imported here, even though they live under this package, because
``judge_config`` needs the optional ``python-dotenv`` dependency at module
scope. Importing any of them here would make every deterministic-only caller
of this package (e.g. ``glassbox.cli``'s ``eval``/``serve``/``export``
subcommands, which import ``glassbox.eval.runner`` and so execute this
``__init__`` first) require the ``judge`` extra to be installed, defeating
the P3a design's "no provider SDK or dotenv tooling for deterministic use"
guarantee. Callers that need judge orchestration import
``glassbox.eval.judge`` directly, exactly as they already do for
``glassbox.eval.judge_config``/``judge_provider``/``judge_models``.
"""

from .models import AssertionResult, DecisionResult, EvidenceRecord, GoldenCase
from .target import EvaluationTarget, load_target

__all__ = [
    "DecisionResult",
    "AssertionResult",
    "EvaluationTarget",
    "EvidenceRecord",
    "GoldenCase",
    "load_target",
]
