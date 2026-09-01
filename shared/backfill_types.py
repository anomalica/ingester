#!/usr/bin/env python3
"""Backfill file_format and document_type onto existing records.

Adds the two fields that split `source_type` (see shared/document_type.py) to
every record already in the store, without re-ingesting:

- file_format: always set, derived from archived_ext (or the audio codec for AV,
  or the source_type's fixed format), normalised (opus, html, epub, pdf, jpg).
- document_type: set ONLY where the title states the form; left absent otherwise.
  The one legacy `document_type: article` written by the old web default is
  stripped, so the column means "derived or human-set", never "defaulted".

Frontmatter-only: it never touches the body or the content_hash, so no record's
identity changes and nothing downstream repoints. Idempotent - a second run is a
no-op. Runs on the host; scans store + store/v1. Dry-run by default; --apply writes.
"""

from __future__ import annotations

import argparse
import collections
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from document_type import (  # noqa: E402
    derive_document_type,
    normalise_file_format,
)


def _store_roots() -> list[Path]:
    override = os.environ.get("INGESTS_STORE")
    base = (
        Path(override).expanduser()
        if override
        else Path(__file__).resolve().parents[2] / "ingests" / "store"
    )
    return [p for p in (base, base / "v1") if p.is_dir()]


def _split(text: str) -> tuple[str, str] | None:
    """(frontmatter, rest-including-closing-marker) or None if not a record."""
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    return parts[1], parts[2]


def _field(fm: str, key: str) -> str | None:
    m = re.search(rf"(?m)^{key}:\s*(.+?)\s*$", fm)
    return m.group(1).strip().strip('"') if m else None


def _derive_file_format(fm: str, source_type: str | None) -> str | None:
    archived = normalise_file_format(_field(fm, "archived_ext"))
    if archived:
        return archived
    if source_type in ("audio", "video"):
        # No archived_ext (transcript-only re-render) - read the kept codec, else
        # opus. Every AV record we hold is opus-in-ogg.
        codec = None
        m = re.search(r"(?m)^\s*codec:\s*(\S+)", fm)
        if m:
            codec = m.group(1)
        return normalise_file_format(codec) or "opus"
    fixed = {
        "web": "html",
        "ebook": "epub",
        "pdf": "pdf",
        "image": "jpg",
        "document": "pdf",  # legacy source_type; the one such record is a PDF
    }.get(source_type or "")
    if fixed:
        return fixed
    # Last resort: a title that is a filename names its own format.
    title = _field(fm, "title") or ""
    return normalise_file_format(Path(title).suffix) if "." in title else None


def plan(fm: str) -> tuple[list[str], str | None, bool]:
    """(lines-to-insert-after-source_type, file_format, strip_legacy_article)."""
    source_type = _field(fm, "source_type")
    title = _field(fm, "title") or ""
    existing_dt = _field(fm, "document_type")

    inserts: list[str] = []
    file_format = _derive_file_format(fm, source_type)
    if file_format and not re.search(r"(?m)^file_format:", fm):
        inserts.append(f"file_format: {file_format}")

    strip_article = existing_dt == "article" and source_type == "web"
    if not existing_dt:
        dt = derive_document_type(source_type or "", title)
        if dt:
            inserts.append(f"document_type: {dt}")
    return inserts, file_format, strip_article


def _apply(fm: str, inserts: list[str], strip_article: bool) -> str:
    if strip_article:
        fm = re.sub(r"(?m)^document_type:\s*article\s*\n", "", fm)
    if not inserts:
        return fm
    lines = fm.split("\n")
    out: list[str] = []
    done = False
    for ln in lines:
        out.append(ln)
        if not done and re.match(r"^source_type:", ln):
            out.extend(inserts)
            done = True
    if not done:  # no source_type line - append before trailing blank
        out.extend(inserts)
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--apply", action="store_true", help="write changes (default: dry-run)"
    )
    args = ap.parse_args()

    roots = _store_roots()
    if not roots:
        print("store not found (set INGESTS_STORE)", file=sys.stderr)
        return 2

    ff_counts: collections.Counter = collections.Counter()
    dt_counts: collections.Counter = collections.Counter()
    changed = absent = stripped = hash_changed = 0
    total = 0

    for root in roots:
        for path in sorted(root.glob("*.md")):
            if path.name.endswith((".verification.json", ".housekeeping.json")):
                continue
            text = path.read_text()
            split = _split(text)
            if not split:
                continue
            total += 1
            fm, rest = split
            hash_before = _field(fm, "content_hash")

            inserts, file_format, strip_article = plan(fm)
            ff_counts[file_format or "(none)"] += 1
            dt_line = next((i for i in inserts if i.startswith("document_type:")), None)
            if dt_line:
                dt_counts[dt_line.split(": ", 1)[1]] += 1
            elif not _field(fm, "document_type") or strip_article:
                absent += 1

            if not inserts and not strip_article:
                continue

            new_fm = _apply(fm, inserts, strip_article)
            if _field(new_fm, "content_hash") != hash_before:
                hash_changed += 1
                print(f"HASH CHANGED (skipped): {path.name}", file=sys.stderr)
                continue
            changed += 1
            if strip_article:
                stripped += 1
            if args.apply:
                path.write_text(f"---{new_fm}---{rest}")

    mode = "APPLIED" if args.apply else "DRY-RUN (use --apply to write)"
    print(f"\n{mode}")
    print(f"records scanned:        {total}")
    print(f"records modified:       {changed}")
    print(f"legacy article stripped:{stripped}")
    print(f"content_hash changed:   {hash_changed}  (must be 0)")
    print("\nfile_format set:")
    for k, v in ff_counts.most_common():
        print(f"  {k:8} {v}")
    dt_set = sum(dt_counts.values())
    print(f"\ndocument_type derived: {dt_set} set, {total - dt_set} absent")
    for k, v in dt_counts.most_common():
        print(f"  {k:12} {v}")
    return 1 if hash_changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
