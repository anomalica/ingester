"""Record writing utilities - store files and human-readable symlinks."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


def slugify(text: str, max_length: int = 60) -> str:
    """Convert text to a URL-safe slug."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    if len(text) > max_length:
        text = text[:max_length].rsplit("-", 1)[0]
    return text


def symlink_name(date: str, source_type: str, title: str) -> str:
    """Generate the human-readable symlink filename."""
    slug = slugify(title)
    return f"{date}-{source_type}-{slug}.md"


def write_record(
    store_dir: Path,
    records_dir: Path,
    hex_hash: str,
    content: str,
    metadata: dict,
    date: str,
    source_type: str,
    title: str,
) -> tuple[Path, Path]:
    """Write a record to the store and create a symlink in records/.

    Returns:
        Tuple of (record_path, symlink_path).
    """
    store_dir.mkdir(parents=True, exist_ok=True)
    records_dir.mkdir(parents=True, exist_ok=True)

    record_path = store_dir / f"{hex_hash}.md"
    meta_path = store_dir / f"{hex_hash}.meta.json"

    record_path.write_text(content)
    meta_path.write_text(json.dumps(metadata, indent=2))

    link_name = symlink_name(date, source_type, title)
    link_path = records_dir / link_name

    if link_path.exists() or link_path.is_symlink():
        link_path.unlink()

    rel_target = os.path.relpath(record_path, records_dir)
    link_path.symlink_to(rel_target)

    return record_path, link_path
