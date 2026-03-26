#!/usr/bin/env python3
"""Web article ingester - extracts structured content into Anomalica record format."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from hashing import hash_string, content_hash_label, store_exists
from record import write_record
from validator import validate

from fetch import FETCHERS
from extraction.trafilatura_ext import extract_article


def _build_frontmatter(
    title: str, date: str, url: str, authors: list[str] | None, hex_hash: str
) -> str:
    """Assemble YAML frontmatter for a web record."""
    lines = [
        "---",
        "schema: anomalica/record/1",
    ]
    if ":" in title:
        lines.append(f'title: "{title}"')
    else:
        lines.append(f"title: {title}")
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


def run(url: str, output_dir: Path, force: bool) -> int:
    """Run the web ingestion pipeline. Returns 0 on success, 1 on failure."""
    store_dir = output_dir / "store"
    records_dir = output_dir / "records"
    start_time = time.monotonic()

    # Try each fetcher in order. After each successful fetch, attempt
    # extraction. If extraction fails (paywall, cookie wall), try the
    # next fetcher.
    article = None
    fetch_method = None

    for method_name, fetcher in FETCHERS:
        print(f"Trying {method_name} fetch...", file=sys.stderr)
        html = fetcher(url)
        if html is None:
            print(f"  {method_name}: no response", file=sys.stderr)
            continue
        print(f"  {method_name}: got HTML, extracting...", file=sys.stderr)
        article = extract_article(html, url)
        if article is None:
            print(f"  {method_name}: no article content extracted", file=sys.stderr)
            continue
        fetch_method = method_name
        print(f"  {method_name}: success", file=sys.stderr)
        break

    if article is None:
        print("All fetch methods exhausted - no content extracted", file=sys.stderr)
        return 1

    print(f"Extracted via {fetch_method}: {article.title}", file=sys.stderr)

    # Hash and check idempotency
    hex_hash = hash_string(article.text)

    if not force and store_exists(store_dir, hex_hash):
        print(
            f"Skipping: record already exists (hash: {hex_hash[:12]}...)",
            file=sys.stderr,
        )
        return 0

    # Build record content
    date = article.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title = article.title or "Untitled"
    frontmatter = _build_frontmatter(title, date, url, article.authors, hex_hash)
    content = frontmatter + "\n\n" + article.text + "\n"

    # Validate
    result = validate(content, extra_required=["source_url"])
    if result.fixed:
        content = result.fixed
    for warning in result.warnings:
        print(f"Validation warning: {warning}", file=sys.stderr)
    for error in result.errors:
        print(f"Validation error: {error}", file=sys.stderr)

    # Write output
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
        description="Extract content from a web article into Anomalica record format."
    )
    parser.add_argument("url", help="URL of the web article to extract")
    parser.add_argument(
        "--force", action="store_true", help="Re-extract even if output exists"
    )
    args = parser.parse_args()

    output_dir = Path("/mnt/output")
    if not output_dir.exists():
        output_dir = Path("output")

    sys.exit(run(args.url, output_dir, args.force))


if __name__ == "__main__":
    main()
