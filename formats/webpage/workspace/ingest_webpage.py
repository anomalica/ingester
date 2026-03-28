#!/usr/bin/env python3
"""Webpage ingester - extracts structured content from pre-fetched HTML."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from hashing import content_hash_label, hash_string, store_exists
from record import write_record
from validator import validate

from extraction.trafilatura_ext import extract_article


def _build_frontmatter(
    title: str, date: str, url: str, authors: list[str] | None, hex_hash: str
) -> str:
    """Assemble YAML frontmatter for a web record."""
    lines = [
        "---",
        "schema: anomalica/record/1",
    ]
    escaped_title = title.replace('"', '\\"')
    lines.append(f'title: "{escaped_title}"')
    lines.extend(
        [
            f"date: {date}",
            "source_type: web",
            f"source_url: {url}",
        ]
    )
    if authors:
        lines.append("authors:")
        for author in authors:
            lines.append(f"  - {author}")
    lines.append(f"content_hash: {content_hash_label(hex_hash)}")
    lines.append("---")
    return "\n".join(lines)


def run(staging_dir: Path, output_dir: Path, force: bool) -> int:
    """Run the webpage ingestion pipeline. Returns 0 on success, 1 on failure."""
    store_dir = output_dir / "store"
    records_dir = output_dir / "records"
    start_time = time.monotonic()

    manifest_path = staging_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"Error: no manifest.json in {staging_dir}", file=sys.stderr)
        return 1

    manifest = json.loads(manifest_path.read_text())
    url = manifest["source"]
    asset_name = manifest["asset"]
    fetch_method = manifest.get("fetch_method", "unknown")

    asset_path = staging_dir / asset_name
    if not asset_path.exists():
        print(f"Error: asset not found: {asset_path}", file=sys.stderr)
        return 1

    html = asset_path.read_text(encoding="utf-8", errors="replace")

    article = extract_article(html, url)
    if article is None:
        print("No article content extracted", file=sys.stderr)
        return 1

    print(f"Extracted: {article.title}", file=sys.stderr)

    hex_hash = hash_string(article.text)

    if not force and store_exists(store_dir, hex_hash):
        print(
            f"Skipping: record already exists (hash: {hex_hash[:12]}...)",
            file=sys.stderr,
        )
        return 0

    date = article.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title = article.title or "Untitled"
    frontmatter = _build_frontmatter(title, date, url, article.authors, hex_hash)
    content = frontmatter + "\n\n" + article.text + "\n"

    result = validate(content, extra_required=["source_url"])
    if result.fixed:
        content = result.fixed
    for warning in result.warnings:
        print(f"Validation warning: {warning}", file=sys.stderr)
    for error in result.errors:
        print(f"Validation error: {error}", file=sys.stderr)

    duration_ms = int((time.monotonic() - start_time) * 1000)
    metadata = {
        "input_url": url,
        "input_hash": content_hash_label(hex_hash),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "fetch_method": fetch_method,
        "duration_ms": duration_ms,
        "trafilatura_metadata": {
            "title": article.title,
            "authors": article.authors,
            "date": article.date,
            "sitename": article.sitename,
            "description": article.description,
        },
    }

    record_path, link_path = write_record(
        store_dir, records_dir, hex_hash, content, metadata, date, "web", title
    )
    print(f"Written: {record_path}", file=sys.stderr)
    print(f"Symlink: {link_path}", file=sys.stderr)
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Extract content from pre-fetched HTML into Anomalica record format."
    )
    parser.add_argument("staging_dir", type=Path, help="Path to staging directory")
    parser.add_argument(
        "--force", action="store_true", help="Re-extract even if output exists"
    )
    args = parser.parse_args()

    output_dir = Path("/mnt/output")
    if not output_dir.exists():
        output_dir = Path(__file__).resolve().parent.parent.parent.parent / "output"

    sys.exit(run(args.staging_dir, output_dir, args.force))


if __name__ == "__main__":
    main()
