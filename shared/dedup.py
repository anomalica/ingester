"""Find existing records by source_id or source_url.

Layered dedup pipeline (cheapest first):
  1. source_id   - stable platform identifier (e.g. youtube:ZBtMbBPzqHY)
  2. source_url  - exact URL match
  3. content_hash - byte hash of the fetched asset (caller's responsibility)

Source-side checks (1 and 2) avoid downloading and processing entirely when
a record already exists. The content-hash check is the last line of defence
inside each format handler.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _read_frontmatter(record_path: Path) -> dict | None:
    """Parse the YAML frontmatter from a record file. Returns None on error."""
    try:
        content = record_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        data = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def _iter_records(store_dir: Path):
    """Yield (path, frontmatter) for each record in store_dir."""
    if not store_dir.is_dir():
        return
    for path in sorted(store_dir.glob("*.md")):
        fm = _read_frontmatter(path)
        if fm is not None:
            yield path, fm


def find_by_source_id(store_dir: Path, source_id: str) -> Path | None:
    """Return the path of the first record whose source_id matches."""
    if not source_id:
        return None
    for path, fm in _iter_records(store_dir):
        if fm.get("source_id") == source_id:
            return path
    return None


def find_by_source_url(store_dir: Path, source_url: str) -> Path | None:
    """Return the path of the first record whose source_url matches exactly."""
    if not source_url:
        return None
    for path, fm in _iter_records(store_dir):
        if fm.get("source_url") == source_url:
            return path
    return None
