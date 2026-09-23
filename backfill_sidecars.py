#!/usr/bin/env python3
"""Backfill verification sidecars for existing records.

Walks the ingests store, regenerates a `.verification.json` for each record
that doesn't already have one. Reads the record's body directly - does not
re-run any format-specific extraction (so safe to run without API keys, GPU,
or container builds).

The source file (for SHA-256 + size) is resolved through the record's Asset
binding: `assets[0]` for an ordinary record/3, otherwise the legacy implicit
Asset rule (`source_hash` when present, else `content_hash`). Legacy records
without an archived source still get cloze-only sidecars. A composite record/3
fails closed because the legacy sidecar shape cannot prove possession of several
independently governed Assets.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent / "shared"))

from verification import build_sidecar, needs_sidecar, write_sidecar  # noqa: E402

INGESTS_DIR_DEFAULT = Path(__file__).resolve().parent.parent / "ingests"
RECORDS_DIR_DEFAULT = Path(__file__).resolve().parent.parent / "records"

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _frontmatter(record: str) -> dict:
    m = FRONTMATTER_RE.match(record)
    if not m:
        return {}
    try:
        value = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}
    return value if isinstance(value, dict) else {}


def _find_record(
    records_dir: Path, asset_hash: str | None, archived_ext: str | None = None
) -> Path | None:
    if not asset_hash:
        return None
    bare = asset_hash.removeprefix("sha256:")
    if archived_ext:
        expected = records_dir / f"{bare}.{archived_ext}"
        if expected.is_file():
            return expected
    matches = sorted(
        path
        for path in records_dir.glob(f"{bare}.*")
        if path.is_file() and path.stem == bare
    )
    return matches[0] if len(matches) == 1 else None


def _archive_binding(fm: dict) -> tuple[str | None, str | None, int | None]:
    if fm.get("schema") == "anomalica/record/3":
        assets = fm.get("assets")
        if (
            not isinstance(assets, list)
            or len(assets) != 1
            or not isinstance(assets[0], dict)
        ):
            raise ValueError(
                "record/3 sidecar backfill requires exactly one selected Asset"
            )
        asset = assets[0]
        pages = asset.get("pages")
        return (
            asset.get("asset_hash"),
            asset.get("archived_ext"),
            pages if isinstance(pages, int) and not isinstance(pages, bool) else None,
        )
    pages = fm.get("pages")
    return (
        fm.get("source_hash") or fm.get("content_hash"),
        fm.get("archived_ext"),
        pages if isinstance(pages, int) and not isinstance(pages, bool) else None,
    )


def _page_count_from_record(record: str) -> int | None:
    m = re.search(r"^pages:\s*(\d+)\s*$", record, flags=re.MULTILINE)
    return int(m.group(1)) if m else None


def _duration_from_record(record: str) -> float | None:
    m = re.search(r"^duration:\s*([\d.]+)\s*$", record, flags=re.MULTILINE)
    return float(m.group(1)) if m else None


def backfill(ingests_dir: Path, records_dir: Path, force: bool) -> int:
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
            asset_hash, archived_ext, page_count = _archive_binding(fm)
            source_path = _find_record(records_dir, asset_hash, archived_ext)
            if fm.get("schema") == "anomalica/record/3" and source_path is None:
                raise ValueError("record/3 held Asset is missing or ambiguous")
            sidecar = build_sidecar(
                record,
                source_path=source_path,
                page_count=page_count or _page_count_from_record(record),
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
        default=RECORDS_DIR_DEFAULT,
        help=f"Path to the record archive (default: {RECORDS_DIR_DEFAULT})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate sidecars even when one already exists",
    )
    args = parser.parse_args()
    sys.exit(backfill(args.ingests_dir, args.records_dir, args.force))


if __name__ == "__main__":
    main()
