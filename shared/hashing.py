"""SHA-256 hashing and output store path utilities."""

from __future__ import annotations

import hashlib
from pathlib import Path


def hash_bytes(data: bytes) -> str:
    """SHA-256 hex digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def hash_string(text: str) -> str:
    """SHA-256 hex digest of a UTF-8 string."""
    return hash_bytes(text.encode("utf-8"))


def hash_file(path: Path) -> str:
    """SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def content_hash_label(hex_hash: str) -> str:
    """Format a hex hash as a content_hash value: sha256:HEXVALUE."""
    return f"sha256:{hex_hash}"


def source_id_to_filename(source_id: str) -> str:
    """Convert a source_id like 'youtube:ZBtMbBPzqHY' to a filename-safe form.

    Replaces colons with hyphens. Result is used as the store filename
    stem (without .md extension) for URL-based content.
    """
    return source_id.replace(":", "-")


def store_path(store_dir: Path, hex_hash: str, suffix: str = ".md") -> Path:
    """Path to a file in the output store."""
    return store_dir / f"{hex_hash}{suffix}"


def store_exists(store_dir: Path, hex_hash: str) -> bool:
    """Check whether a record already exists in the store."""
    return store_path(store_dir, hex_hash).exists()
