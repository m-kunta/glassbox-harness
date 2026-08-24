"""Configurable redaction hooks for sensitive trace content."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, TypeAlias

RedactionHook: TypeAlias = Callable[[Any], Any]


class Redactor:
    """Apply configured redaction hooks in registration order."""

    def __init__(self, hooks: Iterable[RedactionHook] = ()) -> None:
        self._hooks = tuple(hooks)

    def apply(self, value: Any) -> Any:
        """Return *value* after each configured redaction hook has run."""
        redacted_value = value
        for hook in self._hooks:
            redacted_value = hook(redacted_value)
        return redacted_value
