from __future__ import annotations

import hashlib
from pathlib import Path

from glassbox.events import TraceEvent
from glassbox.sdk.config import init, redact, reset_for_testing
from glassbox.sdk.redaction import Redactor
from glassbox.store.blobs import BlobStore


def test_apply_runs_configured_hooks_in_order() -> None:
    calls: list[str] = []

    def remove_supplier(value: str) -> str:
        calls.append("supplier")
        return value.replace("Acme", "[SUPPLIER]")

    def remove_cost(value: str) -> str:
        calls.append("cost")
        return value.replace("$12.50", "[COST]")

    redactor = Redactor((remove_supplier, remove_cost))

    assert redactor.apply("Acme quote: $12.50") == "[SUPPLIER] quote: [COST]"
    assert calls == ["supplier", "cost"]


def test_apply_without_hooks_preserves_the_value() -> None:
    value = {"planner_id": "planner-42"}

    assert Redactor().apply(value) is value


def test_redact_and_deep_freeze_traverse_the_same_container_shapes() -> None:
    """redact() (sdk/config.py) and TraceEvent.attributes' freeze/validation
    walk (events/models.py's _deep_freeze) independently recurse through a
    payload tree. If a future change teaches one to traverse into a new
    container type without the other, values inside it would validate and
    persist correctly while silently bypassing configured redaction hooks --
    an unredacted leak. Pin that today they agree on the traversable shapes:
    a dict/list/tuple mix.
    """
    from datetime import UTC, datetime

    payload = {"a": ["leaf-1", ("leaf-2", {"b": "leaf-3"})]}

    reset_for_testing()
    try:
        init(
            agent="x",
            version="1",
            env="dev",
            redaction_hooks=[lambda value: f"[{value}]" if isinstance(value, str) else value],
        )
        redacted = redact(payload)
    finally:
        reset_for_testing()

    assert redacted == {"a": ["[leaf-1]", ("[leaf-2]", {"b": "[leaf-3]"})]}

    # The exact same container shape must be accepted by the store-bound
    # freeze/validation path -- proving both walks currently traverse the
    # identical set of container types (Mapping, list, tuple).
    event = TraceEvent(
        trace_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
        agent_name="x",
        agent_version="1",
        started_at=datetime(2026, 8, 22, 14, 30, 45, tzinfo=UTC),
        environment="dev",
        attributes=payload,
    )
    assert event.attributes["a"][1][1]["b"] == "leaf-3"


def test_redaction_happens_before_content_is_hashed_and_persisted(tmp_path: Path) -> None:
    redactor = Redactor((lambda value: value.replace("planner-42", "[PLANNER]"),))
    store = BlobStore(tmp_path / "blobs")

    redacted_content = redactor.apply("planner=planner-42").encode()
    ref = store.put(redacted_content)

    assert ref == hashlib.sha256(b"planner=[PLANNER]").hexdigest()
    assert ref != hashlib.sha256(b"planner=planner-42").hexdigest()
    assert store.get(ref) == b"planner=[PLANNER]"
