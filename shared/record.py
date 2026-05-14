"""Record writing utilities - store files and human-readable symlinks."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

INGESTER_VERSION_ENV = "INGESTER_VERSION"


def get_version() -> str:
    """Get the short git commit hash of the ingester repository.

    Reads from the INGESTER_VERSION environment variable first (set by the
    host script for containerised runs where .git/ is not available).
    Falls back to running git rev-parse on the local checkout.
    """
    env_version = os.environ.get(INGESTER_VERSION_ENV)
    if env_version:
        return env_version

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


class SymlinkCollisionError(Exception):
    """Raised when a record symlink would clobber an unrelated existing record.

    Indicates that the upstream dedup pipeline (source_id / source_url /
    content_hash) failed to catch a re-ingest of the same logical source,
    or that two distinct sources produced the same human-readable slug.
    Either way, silently overwriting would orphan the existing store entry.
    """


def write_record(
    store_dir: Path,
    records_dir: Path,
    hex_hash: str,
    content: str,
    date: str,
    source_type: str,
    title: str,
    force: bool = False,
) -> tuple[Path, Path]:
    """Write a record to the store and create a symlink in records/.

    Returns:
        Tuple of (record_path, symlink_path).

    Raises:
        SymlinkCollisionError: if the target symlink already exists and
            points to a different real record. Stale symlinks (broken
            target) and idempotent re-writes (same target) are allowed.
            When ``force`` is True, the colliding symlink AND the old
            record file it points at are deleted before the new record
            is written, since --force re-ingest is an explicit replace.
    """
    store_dir.mkdir(parents=True, exist_ok=True)
    records_dir.mkdir(parents=True, exist_ok=True)

    record_path = store_dir / f"{hex_hash}.md"
    link_name = symlink_name(date, source_type, title)
    link_path = records_dir / link_name

    if link_path.is_symlink():
        existing_target = (records_dir / os.readlink(link_path)).resolve()
        if existing_target.exists() and existing_target != record_path.resolve():
            if not force:
                raise SymlinkCollisionError(
                    f"refusing to overwrite {link_path.name}: "
                    f"already points to {existing_target.name} (a different record). "
                    f"Upstream dedup should have caught this re-ingest."
                )
            # --force replace: drop the stale record file too so it does
            # not orphan in the store. Sidecar files (verification JSON)
            # that share the same hash stem are removed alongside.
            old_stem = existing_target.stem
            for sibling in existing_target.parent.glob(f"{old_stem}*"):
                sibling.unlink()
        link_path.unlink()
    elif link_path.exists():
        link_path.unlink()

    record_path.write_text(content)

    rel_target = os.path.relpath(record_path, records_dir)
    link_path.symlink_to(rel_target)

    return record_path, link_path
