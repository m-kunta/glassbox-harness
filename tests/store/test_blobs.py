from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from glassbox.store.blobs import BlobStore


def test_put_returns_sha256_reference_and_deduplicates_content(tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "blobs")
    content = b"same prompt content"

    first_ref = store.put(content)
    second_ref = store.put(content)

    assert first_ref == hashlib.sha256(content).hexdigest()
    assert second_ref == first_ref
    assert store.get(first_ref) == content
    assert len([path for path in (tmp_path / "blobs").rglob("*") if path.is_file()]) == 1


def test_put_creates_the_blob_with_an_atomic_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = BlobStore(tmp_path / "blobs")
    replacements: list[tuple[Path, Path]] = []
    original_replace = os.replace

    def record_replace(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
    ) -> None:
        replacements.append((Path(source), Path(destination)))
        original_replace(source, destination)

    monkeypatch.setattr("glassbox.store.blobs.os.replace", record_replace)

    ref = store.put(b"atomically persisted")

    assert len(replacements) == 1
    temporary_path, blob_path = replacements[0]
    assert temporary_path.parent == blob_path.parent
    assert temporary_path != blob_path
    assert blob_path.name == ref
    assert store.get(ref) == b"atomically persisted"
    assert not temporary_path.exists()


def test_get_raises_for_a_missing_reference(tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "blobs")

    with pytest.raises(FileNotFoundError):
        store.get("0" * 64)


@pytest.mark.parametrize("ref", ["../outside", "not-a-sha256", "f" * 63])
def test_get_rejects_references_that_cannot_safely_resolve_to_a_blob(
    tmp_path: Path, ref: str
) -> None:
    store = BlobStore(tmp_path / "blobs")

    with pytest.raises(ValueError, match="SHA-256"):
        store.get(ref)
