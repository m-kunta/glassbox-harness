from __future__ import annotations

import hashlib
from pathlib import Path

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


def test_redaction_happens_before_content_is_hashed_and_persisted(tmp_path: Path) -> None:
    redactor = Redactor((lambda value: value.replace("planner-42", "[PLANNER]"),))
    store = BlobStore(tmp_path / "blobs")

    redacted_content = redactor.apply("planner=planner-42").encode()
    ref = store.put(redacted_content)

    assert ref == hashlib.sha256(b"planner=[PLANNER]").hexdigest()
    assert ref != hashlib.sha256(b"planner=planner-42").hexdigest()
    assert store.get(ref) == b"planner=[PLANNER]"
