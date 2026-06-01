#!/usr/bin/env python3
"""Backfill verification sidecars for existing records.

Walks the ingests store, regenerates a `.verification.json` for each record
that doesn't already have one. Reads the record's body directly - does not
re-run any format-specific extraction (so safe to run without API keys, GPU,
or container builds).

The source file (for sha256 + size) is looked up in the sources/ directory by
content_hash field in the record's frontmatter. Records without an archived
source still get a sidecar with cloze challenges only.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "shared"))

from verification import build_sidecar, needs_sidecar, write_sidecar  # noqa: E402

INGESTS_DIR_DEFAULT = Path(__file__).resolve().parent.parent / "ingests"
SOURCES_DIR_DEFAULT = Path(__file__).resolve().parent.parent / "sources"

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _frontmatter(record: str) -> dict:
    m = FRONTMATTER_RE.match(record)
    if not m:
        return {}
    fm: dict = {}
    for line in m.group(1).splitlines():
        if ":" not in line or line.startswith(" "):
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip().strip('"')
    return fm


def _find_source(sources_dir: Path, content_hash: str | None) -> Path | None:
    if not content_hash:
        return None
    bare = content_hash.removeprefix("sha256:")
    matches = list(sources_dir.glob(f"{bare}.*"))
    return matches[0] if matches else None


def _page_count_from_record(record: str) -> int | None:
    m = re.search(r"^pages:\s*(\d+)\s*$", record, flags=re.MULTILINE)
    return int(m.group(1)) if m else None


def _duration_from_record(record: str) -> float | None:
    m = re.search(r"^duration:\s*([\d.]+)\s*$", record, flags=re.MULTILINE)
    return float(m.group(1)) if m else None


def backfill(ingests_dir: Path, sources_dir: Path, force: bool) -> int:
    store_dir = ingests_dir / "store"
    if not store_dir.exists():
        print(f"Error: store directory not found: {store_dir}", file=sys.stderr)
        return 1

    records = sorted(store_dir.glob("*.md"))
    print(f"Found {len(records)} records in {store_dir}", file=sys.stderr)

    written = 0
    skipped = 0
    removed = 0
    failed = 0

    for record_path in records:
        hex_hash = record_path.stem
        sidecar_path = store_dir / f"{hex_hash}.verification.json"

        try:
            record = record_path.read_text()

            if not needs_sidecar(record):
                if sidecar_path.exists():
                    sidecar_path.unlink()
                    print(f"  {hex_hash[:12]} removed (public)", file=sys.stderr)
                    removed += 1
                else:
                    skipped += 1
                continue

            if sidecar_path.exists() and not force:
                skipped += 1
                continue

            fm = _frontmatter(record)
            source_path = _find_source(sources_dir, fm.get("content_hash"))
            sidecar = build_sidecar(
                record,
                source_path=source_path,
                page_count=_page_count_from_record(record),
                duration_seconds=_duration_from_record(record),
            )
            write_sidecar(store_dir, hex_hash, sidecar)
            challenge_count = len(sidecar.get("challenges", []))
            source_label = source_path.name if source_path else "no source"
            print(
                f"  {hex_hash[:12]} -> {challenge_count} challenges ({source_label})",
                file=sys.stderr,
            )
            written += 1
        except Exception as exc:
            print(f"  {hex_hash[:12]} FAILED: {exc}", file=sys.stderr)
            failed += 1

    print(
        f"\nBackfill: {written} written, {removed} removed, {skipped} skipped, {failed} failed",
        file=sys.stderr,
    )
    return 0 if failed == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill verification sidecars for existing records."
    )
    parser.add_argument(
        "--ingests-dir",
        type=Path,
        default=INGESTS_DIR_DEFAULT,
        help=f"Path to ingests (default: {INGESTS_DIR_DEFAULT})",
    )
    parser.add_argument(
        "--sources-dir",
        type=Path,
        default=SOURCES_DIR_DEFAULT,
        help=f"Path to sources archive (default: {SOURCES_DIR_DEFAULT})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate sidecars even when one already exists",
    )
    args = parser.parse_args()
    sys.exit(backfill(args.ingests_dir, args.sources_dir, args.force))


if __name__ == "__main__":
    main()
