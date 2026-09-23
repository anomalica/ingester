#!/usr/bin/env python3
"""List records gated ONLY because their provenance was missing at ingest.

A local file dropped in the inbox with no source URL and no source id defaults to
copyright.status: restricted, and is then gated forever with nobody told - the
consequence is invisible until someone opens the record in the review app. This
turns that into a QUEUE: a record is in the set when its status is restricted AND
it carries neither a source_url nor a source_id (the signature), which also catches
records ingested before the explanatory `detail` was emitted.

This is distinct from a record restricted because it is a known copyrighted work
(those have provenance). Supply a source URL (a public or government one may release
it) or confirm the licence to clear an item.

Run from the host (it scans the ingests store). Override the store with the
INGESTS_STORE env var.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml


def _store() -> Path:
    override = os.environ.get("INGESTS_STORE")
    if override:
        return Path(override).expanduser()
    # shared/ is ingester/shared; the ingests store is a sibling repo: anomalica/ingests/store
    return Path(__file__).resolve().parents[2] / "ingests" / "store"


def _frontmatter(text: str) -> str:
    parts = text.split("---", 2)
    return parts[1] if len(parts) >= 3 else ""


def _field(fm: str, key: str) -> str | None:
    m = re.search(rf"(?m)^{key}:\s*(.+?)\s*$", fm)
    return m.group(1).strip().strip('"') if m else None


def _record3_missing_provenance(frontmatter: dict) -> bool:
    assets = frontmatter.get("assets")
    if not isinstance(assets, list) or not assets:
        return False
    statuses = [
        asset.get("copyright", {}).get("status")
        for asset in assets
        if isinstance(asset, dict) and isinstance(asset.get("copyright"), dict)
    ]
    if "restricted" not in statuses:
        return False
    provenance = frontmatter.get("provenance")
    if isinstance(provenance, dict):
        if provenance.get("source_url"):
            return False
        identifiers = provenance.get("identifiers")
        if isinstance(identifiers, dict) and any(identifiers.values()):
            return False
    for asset in assets:
        acquisition = asset.get("acquisition") if isinstance(asset, dict) else None
        if not isinstance(acquisition, dict):
            continue
        if acquisition.get("fetched_url"):
            return False
        identifiers = acquisition.get("copy_identifiers")
        if isinstance(identifiers, dict) and any(identifiers.values()):
            return False
    return True


def find(store: Path) -> list[tuple[str, str, str]]:
    hits = []
    for path in sorted(store.glob("*.md")):
        name = path.name
        if name.endswith((".verification.json", ".housekeeping.json")):
            continue
        fm = _frontmatter(path.read_text())
        try:
            frontmatter = yaml.safe_load(fm)
        except yaml.YAMLError:
            frontmatter = None
        if (
            isinstance(frontmatter, dict)
            and frontmatter.get("schema") == "anomalica/record/3"
        ):
            if not _record3_missing_provenance(frontmatter):
                continue
            source_types = frontmatter.get("source_types")
            source_type = (
                source_types[0]
                if isinstance(source_types, list) and source_types
                else "?"
            )
            hits.append(
                (
                    name.split(".")[0][:16],
                    source_type,
                    str(frontmatter.get("title") or "(no title)"),
                )
            )
            continue
        if not re.search(r"(?m)^\s*status:\s*restricted\b", fm):
            continue
        if re.search(r"(?m)^source_url:", fm) or re.search(r"(?m)^source_id:", fm):
            continue  # has provenance -> restricted for a real reason, not this queue
        hits.append(
            (
                name.split(".")[0][:16],
                _field(fm, "source_type") or "?",
                _field(fm, "title") or "(no title)",
            )
        )
    return hits


def main() -> int:
    store = _store()
    if not store.is_dir():
        print(f"store not found: {store} (set INGESTS_STORE)", file=sys.stderr)
        return 2
    hits = find(store)
    for h, stype, title in hits:
        print(f"{h}  [{stype}]  {title[:70]}")
    print(
        f"\n{len(hits)} record(s) gated ONLY for missing provenance. "
        "Supply a source URL (or confirm the licence) to release each."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
