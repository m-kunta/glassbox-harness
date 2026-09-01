"""Import evaluation targets without coupling the evaluator to an agent."""

from __future__ import annotations

import importlib
from typing import Protocol, cast

from .models import DecisionResult, GoldenCase


class EvaluationTarget(Protocol):
    """A callable adapter that executes one labeled case."""

    def __call__(self, case: GoldenCase) -> DecisionResult: ...


def load_target(import_path: str) -> EvaluationTarget:
    """Load a callable target from one ``module:attribute`` import path."""
    module_name, separator, attribute_name = import_path.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ValueError("target must use the form 'module:attribute'")

    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ValueError(f"unable to import evaluation target module {module_name!r}") from exc

    try:
        target = getattr(module, attribute_name)
    except AttributeError as exc:
        raise ValueError(f"evaluation target {import_path!r} does not exist") from exc

    if not callable(target):
        raise ValueError(f"evaluation target {import_path!r} is not callable")
    return cast(EvaluationTarget, target)
