"""Import evaluation targets without coupling the evaluator to an agent."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Protocol, cast

from .models import DecisionResult, GoldenCase


class EvaluationTarget(Protocol):
    """A callable adapter that executes one labeled case."""

    def __call__(self, case: GoldenCase) -> DecisionResult: ...


def load_target(import_path: str, *, module_root: Path | None = None) -> EvaluationTarget:
    """Load a callable target from one ``module:attribute`` import path."""
    module_name, separator, attribute_name = import_path.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ValueError("target must use the form 'module:attribute'")

    try:
        module = _load_module(module_name, module_root)
    except ImportError as exc:
        raise ValueError(f"unable to import evaluation target module {module_name!r}") from exc

    try:
        target = getattr(module, attribute_name)
    except AttributeError as exc:
        raise ValueError(f"evaluation target {import_path!r} does not exist") from exc

    if not callable(target):
        raise ValueError(f"evaluation target {import_path!r} is not callable")
    return cast(EvaluationTarget, target)


def _load_module(module_name: str, module_root: Path | None) -> object:
    """Load a manifest-local adapter without changing global import state."""
    if module_root is not None:
        relative = Path(*module_name.split("."))
        module_path = module_root / relative.with_suffix(".py")
        package_path = module_root / relative / "__init__.py"
        source_path = module_path if module_path.is_file() else package_path
        if source_path.is_file():
            spec = importlib.util.spec_from_file_location(
                module_name,
                source_path,
                submodule_search_locations=[str(source_path.parent)]
                if source_path.name == "__init__.py"
                else None,
            )
            if spec is None or spec.loader is None:
                raise ImportError(f"unable to load {module_name!r}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    return importlib.import_module(module_name)
