#!/usr/bin/env python3
"""Repair PDF records whose file_page markers are complete but misnumbered.

Records ingested before the 2026-08-01 resequencing fix can carry file_page
numbers the extraction model invented (chunk-offset double-counts, or printed
page numbers copied in): non-monotonic, repeating, running past the page count.

Where the marker count EQUALS the page count, every page was extracted once in
order, so the correct file_page values are simply their position - a
deterministic renumber to 1..N, no model and no spend, and character-preserving
apart from the digits. This is the same rule as the handler's
_resequence_pages_sequential, applied to records already on disk.

It does NOT touch records with FEWER markers than pages (a genuinely missing or
merged page - that needs re-extraction, not renumbering) - those are only
reported. file_page lives in the body, and a PDF record's identity is its source
bytes, so a renumber changes no content_hash. Dry-run by default; --apply writes.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


def _store_roots() -> list[Path]:
    override = os.environ.get("INGESTS_STORE")
    base = (
        Path(override).expanduser()
        if override
        else Path(__file__).resolve().parents[2] / "ingests" / "store"
    )
    return [p for p in (base, base / "v1") if p.is_dir()]


def _field(fm: str, key: str) -> str | None:
    m = re.search(rf"(?m)^{key}:\s*(.+?)\s*$", fm)
    return m.group(1).strip().strip('"') if m else None


def _renumber(body: str, page_count: int) -> str:
    counter = iter(range(1, page_count + 1))
    return re.sub(r"file_page: \d+", lambda _m: f"file_page: {next(counter)}", body)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    ap.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="hash prefix to skip (e.g. a record open in the workbench)",
    )
    args = ap.parse_args()

    roots = _store_roots()
    if not roots:
        print("store not found (set INGESTS_STORE)", file=sys.stderr)
        return 2

    fixable: list[tuple[str, int]] = []
    needs_reextraction: list[tuple[str, int, int]] = []
    excluded: list[str] = []

    for root in roots:
        for path in sorted(root.glob("*.md")):
            if path.name.endswith(".json"):
                continue
            text = path.read_text()
            parts = text.split("---", 2)
            if len(parts) < 3:
                continue
            fm, body = parts[1], parts[2]
            if _field(fm, "source_type") not in ("pdf", "image"):
                continue
            markers = re.findall(r"file_page: (\d+)", body)
            if not markers:
                continue
            pages_field = _field(fm, "pages")
            if not pages_field or not pages_field.isdigit():
                continue
            page_count = int(pages_field)
            hexname = path.name.split(".")[0]

            already_ordered = markers == [str(i) for i in range(1, page_count + 1)]
            if already_ordered:
                continue

            if len(markers) != page_count:
                needs_reextraction.append((hexname, len(markers), page_count))
                continue

            if any(hexname.startswith(pre) for pre in args.exclude):
                excluded.append(hexname)
                continue

            # Only the body's file_page digits change; the frontmatter (and so
            # content_hash) is written back verbatim, so identity is preserved by
            # construction.
            new_body = _renumber(body, page_count)
            fixable.append((hexname, page_count))
            if args.apply:
                path.write_text(f"---{fm}---{new_body}")

    mode = "APPLIED" if args.apply else "DRY-RUN (use --apply to write)"
    print(f"\n{mode}\n")
    print(f"complete-but-misnumbered, renumbered to 1..N: {len(fixable)}")
    for h, n in fixable:
        print(f"  {h[:16]}  ({n} pages)")
    if excluded:
        print(f"\nexcluded (named on --exclude): {excluded}")
    print(
        f"\nfewer markers than pages - NEEDS RE-EXTRACTION, not renumbered: "
        f"{len(needs_reextraction)}"
    )
    for h, got, want in needs_reextraction:
        print(f"  {h[:16]}  {got} markers / {want} pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
