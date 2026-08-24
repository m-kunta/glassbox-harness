"""Content-addressed local blob persistence."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

_SHA256_REF = re.compile(r"^[0-9a-f]{64}$")


class BlobStore:
    """Persist bytes by their canonical SHA-256 content reference."""

    def __init__(self, directory: Path | str) -> None:
        self._directory = Path(directory)

    def put(self, content: bytes) -> str:
        """Atomically persist *content* and return its SHA-256 reference."""
        ref = hashlib.sha256(content).hexdigest()
        blob_path = self._path(ref)
        blob_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(dir=blob_path.parent, delete=False) as temporary:
            temporary.write(content)
            temporary_path = Path(temporary.name)

        try:
            os.replace(temporary_path, blob_path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        return ref

    def get(self, ref: str) -> bytes:
        """Return the content identified by *ref*."""
        return self._path(ref).read_bytes()

    def _path(self, ref: str) -> Path:
        if not _SHA256_REF.fullmatch(ref):
            raise ValueError("blob reference must be a lowercase SHA-256 hex digest")

        root = self._directory.resolve()
        path = (root / ref).resolve()
        if not path.is_relative_to(root):
            raise ValueError("blob reference must resolve within the blob directory")
        return path
