"""Record writing utilities - store files and human-readable symlinks."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


def get_version() -> str:
    """Get the short git commit hash of the ingester repository."""
    try:
        repo_root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return "unknown"


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
    record_path.write_text(content)

    link_name = symlink_name(date, source_type, title)
    link_path = records_dir / link_name

    if link_path.exists() or link_path.is_symlink():
        link_path.unlink()

    rel_target = os.path.relpath(record_path, records_dir)
    link_path.symlink_to(rel_target)

    return record_path, link_path
