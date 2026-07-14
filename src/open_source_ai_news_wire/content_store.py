"""Private content-addressed storage for bounded retained artifacts."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StoredContent:
    digest: str
    path: Path
    size: int


class ContentStore:
    def __init__(self, root: Path):
        self.root = root

    def put(self, payload: bytes, *, category: str = "evidence") -> StoredContent:
        if category not in {"evidence", "primary-snapshots", "translations"}:
            raise ValueError("Unsupported content category")
        digest = hashlib.sha256(payload).hexdigest()
        directory = self.root / category / digest[:2]
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        destination = directory / digest
        if not destination.exists():
            descriptor, temporary_name = tempfile.mkstemp(prefix=".write-", dir=directory)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary_name, 0o600)
                os.replace(temporary_name, destination)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)
        return StoredContent(digest=digest, path=destination, size=len(payload))

    def get(self, digest: str, *, category: str = "evidence") -> bytes:
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("Invalid content digest")
        return (self.root / category / digest[:2] / digest).read_bytes()
