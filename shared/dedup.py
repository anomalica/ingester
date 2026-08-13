"""Find existing records by source_id or source_url.

Layered dedup pipeline (cheapest first):
  1. source_id   - stable platform identifier (e.g. youtube:ZBtMbBPzqHY)
  2. source_url  - exact match against every URL a record answers to, including
     the aliases in `also_published_at` and `fetched_url`
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
    """Yield (path, frontmatter) for each LIVE record in store_dir.

    Superseded records (carrying `superseded_by`) are skipped: they are retired,
    so for intake dedup they must not count as an existing copy - a re-ingest of a
    source whose only record has been superseded should be allowed to proceed."""
    if not store_dir.is_dir():
        return
    for path in sorted(store_dir.glob("*.md")):
        fm = _read_frontmatter(path)
        if fm is not None and not fm.get("superseded_by"):
            yield path, fm


# A record names ONE source_url, but the same recording is often published in more
# than one place - an episode on the publisher's channel and a repost elsewhere.
# When two such records are merged, the survivor keeps one source_url and records
# the others: `also_published_at` for the alternative listings, `fetched_url` for
# the one the asset was actually pulled from. Dedup that reads source_url alone
# re-ingests an alias as a fresh record, recreating the duplicate that merging the
# two was meant to remove.
_URL_FIELDS = ("source_url", "fetched_url", "also_published_at")


def _urls_of(fm: dict) -> set[str]:
    """Every URL a record answers to, aliases included.

    Read from the top level AND from the `provenance` block. Decision 0043 makes
    `provenance` the canonical home for source-origin metadata and the store is
    mid-migration, so records of both shapes sit side by side; reading one shape
    only means dedup quietly stops recognising a record the day it is migrated.
    """
    urls: set[str] = set()
    prov = fm.get("provenance")
    for d in [fm, prov] if isinstance(prov, dict) else [fm]:
        for key in _URL_FIELDS:
            value = d.get(key)
            # `also_published_at` is a list; the others are single strings.
            for v in value if isinstance(value, list) else [value]:
                if isinstance(v, str) and v.strip():
                    urls.add(v.strip())
    return urls


def find_by_source_id(store_dir: Path, source_id: str) -> Path | None:
    """Return the path of the first LIVE record whose source_id matches."""
    if not source_id:
        return None
    for path, fm in _iter_records(store_dir):
        if fm.get("source_id") == source_id:
            return path
    return None


def find_by_source_url(store_dir: Path, source_url: str) -> Path | None:
    """Return the path of the first LIVE record published at this URL.

    Matching is EXACT, and deliberately so: this is the cheap pre-check that
    avoids a download, not the authority on identity. The scheduler holds the
    canonicaliser that collapses `?v=ID&t=90s`, `youtu.be/ID` and
    `source_id: youtube:ID` to one key, and content_hash inside each format
    handler is the last line of defence. A variant URL that slips past here is
    caught there; what must not happen is missing an alias we can plainly see.
    """
    if not source_url:
        return None
    for path, fm in _iter_records(store_dir):
        if source_url in _urls_of(fm):
            return path
    return None
