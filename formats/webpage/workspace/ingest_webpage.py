#!/usr/bin/env python3
"""Webpage ingester - extracts structured content from pre-fetched HTML."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from hashing import content_hash_label, hash_string, store_exists
from record import get_version, write_record
from validator import validate

from extraction.trafilatura_ext import extract_article


def _get_trafilatura_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("trafilatura")
    except PackageNotFoundError:
        return "unknown"


def _build_frontmatter(
    title: str,
    date_published: str,
    url: str,
    source_id: str | None,
    fetched_url: str | None,
    date_accessed: str | None,
    authors: list[str] | None,
    hex_hash: str,
    publisher: str | None,
    description: str | None,
) -> str:
    """Assemble YAML frontmatter for a web record."""
    escaped_title = title.replace('"', '\\"')
    lines = [
        "---",
        "schema: anomalica/record/1",
        f'title: "{escaped_title}"',
        f"date_published: {date_published}",
        "source_type: web",
        f"source_url: {url}",
    ]
    if publisher:
        escaped_pub = publisher.replace('"', '\\"')
        lines.append(f'publisher: "{escaped_pub}"')
    if source_id:
        lines.append(f"source_id: {source_id}")
    if fetched_url and fetched_url != url:
        lines.append(f"fetched_url: {fetched_url}")
    if authors:
        lines.append("authors:")
        for author in authors:
            lines.append(f"  - {author}")
    if description:
        escaped_desc = description.replace('"', '\\"')
        lines.append(f'description: "{escaped_desc}"')
    lines.append(f"content_hash: {content_hash_label(hex_hash)}")
    if date_accessed:
        lines.append(f"date_accessed: {date_accessed}")
    lines.append(f"date_extracted: {datetime.now(timezone.utc).isoformat()}")
    lines.append("copyright:")
    lines.append("  status: publicly_accessible")
    lines.append("processing:")
    lines.append("  handler: webpage")
    lines.append(f"  version: {get_version()}")
    lines.append("  tools:")
    lines.append("    - name: trafilatura")
    lines.append(f'      version: "{_get_trafilatura_version()}"')
    lines.append("      role: extraction")
    lines.append("      provider: local")
    lines.append("---")
    return "\n".join(lines)


def run(staging_dir: Path, output_dir: Path, force: bool) -> int:
    """Run the webpage ingestion pipeline. Returns 0 on success, 1 on failure."""
    store_dir = output_dir / "store"
    records_dir = output_dir / "records"

    manifest_path = staging_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"Error: no manifest.json in {staging_dir}", file=sys.stderr)
        return 1

    manifest = json.loads(manifest_path.read_text())
    url = manifest["source"]
    asset_name = manifest["asset"]
    source_id = manifest.get("source_id")
    fetched_url = manifest.get("fetched_url")

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

    # Web articles are URL-based: use source_id from acquire as the store key.
    # Falls back to content hash only if source_id is missing for some reason.
    if not force and store_exists(store_dir, hex_hash):
        print(
            f"Skipping: record already exists (hash: {hex_hash[:12]}...)",
            file=sys.stderr,
        )
        return 0

    date_published = article.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    date_accessed = manifest.get("fetched_at")
    title = article.title or "Untitled"
    frontmatter = _build_frontmatter(
        title,
        date_published,
        url,
        source_id,
        fetched_url,
        date_accessed,
        article.authors,
        hex_hash,
        article.sitename,
        article.description,
    )
    content = frontmatter + "\n\n" + article.text + "\n"

    result = validate(content, extra_required=["source_url"])
    if result.fixed:
        content = result.fixed
    for warning in result.warnings:
        print(f"Validation warning: {warning}", file=sys.stderr)
    for error in result.errors:
        print(f"Validation error: {error}", file=sys.stderr)

    record_path, link_path = write_record(
        store_dir, records_dir, hex_hash, content, date_published, "web", title
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
        output_dir = (
            Path(__file__).resolve().parent.parent.parent.parent.parent
            / "anomalica-ingests"
        )

    sys.exit(run(args.staging_dir, output_dir, args.force))


if __name__ == "__main__":
    main()
