"""Find live Records by Asset bytes or non-authoritative source locators.

Only an exact Asset hash plus canonical Selection establishes Record identity.
``source_id`` and URL matches remain useful candidate lookups for operations and
result recovery, but ordinary acquisition must not suppress a new Asset because
one of those mutable locators already appears on another Record.
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
    for path in sorted(store_dir.rglob("*.md")):
        if "legacy-identities" in path.relative_to(store_dir).parts:
            continue
        fm = _read_frontmatter(path)
        if (
            fm is not None
            and not fm.get("superseded_by")
            and not fm.get("retired_into")
        ):
            yield path, fm


# A record names ONE source_url, but the same recording is often published in more
# than one place - an episode on the publisher's channel and a repost elsewhere.
# When two such records are merged, the survivor keeps one source_url and records
# the others: `also_published_at` for the alternative listings, `fetched_url` for
# the one the asset was actually pulled from. Dedup that reads source_url alone
# re-ingests an alias as a fresh record, recreating the duplicate that merging the
# two was meant to remove. Candidate lookup must therefore inspect each alias.
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
    for asset in fm.get("assets") or []:
        acquisition = asset.get("acquisition") if isinstance(asset, dict) else None
        fetched = (
            acquisition.get("fetched_url") if isinstance(acquisition, dict) else None
        )
        if isinstance(fetched, str) and fetched.strip():
            urls.add(fetched.strip())
    return urls


def find_by_source_hash(store_dir: Path, source_hash: str) -> Path | None:
    """Return the path of the LIVE record extracted from exactly these source
    bytes (`source_hash`, with or without its sha256: label). A hit means a
    re-ingest of the same asset is an in-place refresh, not a new record."""
    wanted = (source_hash or "").removeprefix("sha256:")
    if not wanted:
        return None
    for path, fm in _iter_records(store_dir):
        if fm.get("schema") == "anomalica/record/3":
            # Ordinary acquisition deduplicates only the default one-element
            # whole-Asset Selection. A page-only structural child must not block
            # creation of the default Record for newly acquired bytes.
            selection = fm.get("selection")
            if selection == [
                {
                    "asset_hash": f"sha256:{wanted}",
                    "selector": {"type": "whole"},
                }
            ]:
                return path
            continue
        # ADR 0051's legacy interpretation is source_hash when present,
        # otherwise content_hash, for every record/1-/2 source type.
        implicit = fm.get("source_hash") or fm.get("content_hash")
        if str(implicit or "").removeprefix("sha256:") == wanted:
            return path
    return None


def find_by_source_id(store_dir: Path, source_id: str) -> Path | None:
    """Return the path of the first LIVE record whose source_id matches."""
    if not source_id:
        return None
    for path, fm in _iter_records(store_dir):
        if fm.get("source_id") == source_id:
            return path
        provenance = fm.get("provenance")
        identifiers = (
            provenance.get("identifiers") if isinstance(provenance, dict) else None
        )
        if isinstance(identifiers, dict) and identifiers.get("source_id") == source_id:
            return path
        for asset in fm.get("assets") or []:
            acquisition = asset.get("acquisition") if isinstance(asset, dict) else None
            copy_identifiers = (
                acquisition.get("copy_identifiers")
                if isinstance(acquisition, dict)
                else None
            )
            if (
                isinstance(copy_identifiers, dict)
                and copy_identifiers.get("source_id") == source_id
            ):
                return path
    return None


def find_by_source_url(store_dir: Path, source_url: str) -> Path | None:
    """Return the first LIVE candidate that records this exact URL.

    This locator lookup is not identity evidence and must not by itself prevent
    acquisition or creation of a different canonical Selection.
    """
    if not source_url:
        return None
    for path, fm in _iter_records(store_dir):
        if source_url in _urls_of(fm):
            return path
    return None
