#!/usr/bin/env python3
"""Ebook ingester - converts EPUB into Anomalica record format."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from hashing import content_hash_label, hash_string, store_exists
from record import body_prelude, get_version, write_record
from validator import validate
from verification import build_sidecar, needs_sidecar, write_sidecar

from extraction.epub_extract import ExtractedBook, extract


def _ebooklib_version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("ebooklib")
    except PackageNotFoundError:
        return "unknown"


def _normalise_date(raw: str | None) -> str:
    if not raw:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    candidate = raw.strip()
    for length in (10, 7, 4):
        prefix = candidate[:length]
        try:
            datetime.strptime(
                prefix, "%Y-%m-%d" if length == 10 else "%Y-%m" if length == 7 else "%Y"
            )
            return prefix
        except ValueError:
            continue
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _build_frontmatter(
    book: ExtractedBook,
    date_published: str,
    source_url: str | None,
    date_accessed: str | None,
    hex_hash: str,
    media_summary: dict | None,
) -> str:
    escaped_title = book.title.replace('"', '\\"')
    lines = [
        "---",
        "schema: anomalica/record/1",
        f'title: "{escaped_title}"',
        f"date_published: {date_published}",
        "source_type: ebook",
    ]
    if book.publisher:
        escaped_pub = book.publisher.replace('"', '\\"')
        lines.append(f'publisher: "{escaped_pub}"')
    if source_url:
        lines.append(f"source_url: {source_url}")
    if book.identifier:
        lines.append(f"source_id: {book.identifier}")
    if book.authors:
        lines.append("authors:")
        for author in book.authors:
            lines.append(f"  - {author}")
    if book.description:
        escaped_desc = book.description.replace('"', '\\"')
        lines.append(f'description: "{escaped_desc}"')
    lines.append(f"content_hash: {content_hash_label(hex_hash)}")
    if date_accessed:
        lines.append(f"date_accessed: {date_accessed}")
    lines.append(f"date_extracted: {datetime.now(timezone.utc).isoformat()}")
    lines.append("copyright:")
    lines.append("  status: licensed")
    if media_summary:
        lines.append("media:")
        lines.append(f"  count: {media_summary['count']}")
        lines.append(f"  total_bytes: {media_summary['total_bytes']}")
    lines.append("processing:")
    lines.append("  handler: ebook")
    lines.append(f"  version: {get_version()}")
    lines.append("  tools:")
    lines.append("    - name: ebooklib")
    lines.append(f'      version: "{_ebooklib_version()}"')
    lines.append("      role: extraction")
    lines.append("      provider: local")
    if book.language:
        lines.append(f"  language: {book.language}")
    lines.append("---")
    return "\n".join(lines)


def _render_body(book: ExtractedBook) -> str:
    parts: list[str] = []
    for chapter in book.chapters:
        annotation = [f"<!-- chapter: {chapter.index} -->"]
        if chapter.title:
            escaped = chapter.title.replace('"', '\\"')
            annotation.append(f'<!-- chapter_title: "{escaped}" -->')
        parts.append("\n".join(annotation))
        parts.append(chapter.markdown)
    return "\n\n".join(parts) + "\n"


def run(staging_dir: Path, output_dir: Path, force: bool) -> int:
    store_dir = output_dir / "store"
    records_dir = output_dir / "records"

    manifest_path = staging_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"Error: no manifest.json in {staging_dir}", file=sys.stderr)
        return 1

    manifest = json.loads(manifest_path.read_text())
    asset_name = manifest["asset"]
    source_url = manifest.get("source_url") or manifest.get("source")
    if isinstance(source_url, str) and not source_url.startswith(
        ("http://", "https://")
    ):
        source_url = None
    date_accessed = manifest.get("fetched_at")

    asset_path = staging_dir / asset_name
    if not asset_path.exists():
        print(f"Error: asset not found: {asset_path}", file=sys.stderr)
        return 1

    book = extract(str(asset_path))
    if not book.chapters:
        print("No chapters extracted", file=sys.stderr)
        return 1

    print(
        f"Extracted: {book.title} ({len(book.chapters)} chapters, {len(book.images)} images)",
        file=sys.stderr,
    )

    body = _render_body(book)
    hex_hash = hash_string(body)

    if not force and store_exists(store_dir, hex_hash):
        print(
            f"Skipping: record already exists (hash: {hex_hash[:12]}...)",
            file=sys.stderr,
        )
        return 0

    media_summary = None
    if book.images:
        media_summary = {
            "count": len(book.images),
            "total_bytes": sum(len(img.bytes) for img in book.images),
        }

    date_published = _normalise_date(book.date_published)
    frontmatter = _build_frontmatter(
        book, date_published, source_url, date_accessed, hex_hash, media_summary
    )
    prelude = body_prelude(book.title, date_published)
    content = frontmatter + "\n\n" + prelude + "\n\n" + body

    result = validate(content)
    if result.fixed:
        content = result.fixed
    for warning in result.warnings:
        print(f"Validation warning: {warning}", file=sys.stderr)
    for error in result.errors:
        print(f"Validation error: {error}", file=sys.stderr)

    record_path, link_path = write_record(
        store_dir,
        records_dir,
        hex_hash,
        content,
        date_published,
        "ebook",
        book.title,
        force=force,
    )
    print(f"Written: {record_path}", file=sys.stderr)
    print(f"Symlink: {link_path}", file=sys.stderr)

    if book.images:
        media_dir = output_dir / "media" / hex_hash
        media_dir.mkdir(parents=True, exist_ok=True)
        for img in book.images:
            (media_dir / f"{img.hash}.{img.ext}").write_bytes(img.bytes)
        print(
            f"Media: {len(book.images)} images -> {media_dir}",
            file=sys.stderr,
        )

    if needs_sidecar(content):
        sidecar = build_sidecar(body, source_path=asset_path)
        sidecar_path = write_sidecar(store_dir, hex_hash, sidecar)
        print(
            f"Verification: {sidecar_path.name} ({len(sidecar.get('challenges', []))} challenges)",
            file=sys.stderr,
        )
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Extract structured content from an EPUB into Anomalica record format."
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
