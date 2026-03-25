#!/usr/bin/env python3
"""PDF ingester - extracts structured content from PDFs into Anomalica record format."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from extraction.chunker import get_page_count, split_pdf
from extraction.claude_code import ClaudeCodeProvider
from validator import validate

MAX_PAGES_SINGLE_PASS = 20
CHUNK_SIZE = 20
MIN_CHUNK_SIZE = 5
MAX_RETRIES = 2


def _hash_file(path: Path) -> str:
    """SHA-256 hash of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _should_skip(
    output_file: Path, meta_file: Path, input_hash: str, force: bool
) -> bool:
    """Check if extraction can be skipped based on existing output and hash match."""
    if force:
        return False
    if not output_file.exists():
        return False
    if not meta_file.exists():
        return False
    try:
        meta = json.loads(meta_file.read_text())
        if meta.get("input_hash") == input_hash:
            return True
        print("Input file has changed (hash mismatch), re-extracting", file=sys.stderr)
        return False
    except json.JSONDecodeError:
        return False


def _is_valid_record(content: str, min_chars: int = 500) -> bool:
    """Check that content looks like a valid record with meaningful body text."""
    stripped = content.strip()
    # Strip code fences first
    if stripped.startswith("```"):
        newline = stripped.find("\n")
        if newline >= 0:
            stripped = stripped[newline + 1 :]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
        stripped = stripped.strip()
    if not stripped.startswith("---"):
        return False
    # Check there's meaningful content beyond just frontmatter
    return len(stripped) >= min_chars


def _patch_frontmatter(content: str, input_hash: str, page_count: int) -> str:
    """Inject content_hash and fix page count in the YAML frontmatter."""
    parts = content.split("---", 2)
    if len(parts) < 3:
        return content

    frontmatter = parts[1]

    if "content_hash:" not in frontmatter:
        frontmatter = (
            frontmatter.rstrip("\n") + f"\ncontent_hash: sha256:{input_hash}\n"
        )

    frontmatter = re.sub(r"pages: \d+", f"pages: {page_count}", frontmatter, count=1)

    return f"---{frontmatter}---{parts[2]}"


def _strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter from content, keeping only the body."""
    stripped = content.strip()
    if not stripped.startswith("---"):
        return content
    parts = stripped.split("---", 2)
    if len(parts) >= 3:
        return parts[2]
    return content


def _extract_with_retry(provider, pdf_path: Path) -> tuple[str, dict]:
    """Extract with retries. Validates output is a real record."""
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            content, meta = provider.extract(pdf_path)
            if _is_valid_record(content):
                return content, meta
            print(
                f"Attempt {attempt + 1}: extraction returned non-record output, retrying",
                file=sys.stderr,
            )
            last_error = RuntimeError("Extraction returned non-record output")
        except RuntimeError as e:
            print(f"Attempt {attempt + 1} failed: {e}, retrying", file=sys.stderr)
            last_error = e
    raise last_error


def _extract_chunk_with_retry(
    provider, pdf_data: bytes, page_offset: int, page_count: int
) -> tuple[str, dict]:
    """Extract a chunk with retries. Validates content is proportional to page count."""
    # Expect at least ~200 chars per page as a sanity check
    min_chars = max(500, page_count * 200)
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            content, meta = provider.extract_chunk(pdf_data, page_offset, page_count)
            if _is_valid_record(content, min_chars=min_chars):
                return content, meta
            print(
                f"Chunk attempt {attempt + 1}: content too short "
                f"({len(content.strip())} chars for {page_count} pages, "
                f"expected at least {min_chars}), retrying",
                file=sys.stderr,
            )
            last_error = RuntimeError("Chunk returned insufficient content")
        except RuntimeError as e:
            print(
                f"Chunk attempt {attempt + 1} failed: {e}, retrying",
                file=sys.stderr,
            )
            last_error = e
    raise last_error


def main():
    parser = argparse.ArgumentParser(
        description="Extract content from a PDF into Anomalica record format."
    )
    parser.add_argument("input_file", nargs="?", type=Path, help="Path to the PDF file")
    parser.add_argument("output_dir", nargs="?", type=Path, help="Output directory")
    parser.add_argument(
        "--force", action="store_true", help="Re-process even if output file exists"
    )
    args = parser.parse_args()

    # Auto-detect mount paths from container-magic
    mnt_input = Path("/mnt/input")
    mnt_output = Path("/mnt/output")
    if not args.input_file and mnt_input.exists():
        pdfs = list(mnt_input.glob("*.pdf"))
        if pdfs:
            args.input_file = pdfs[0]
    if not args.output_dir and mnt_output.exists():
        args.output_dir = mnt_output
    if not args.output_dir:
        args.output_dir = Path(".")

    if not args.input_file:
        parser.error("input_file is required (or use cm run ingest input=<file>)")

    if not args.input_file.exists():
        print(f"Error: file not found: {args.input_file}", file=sys.stderr)
        sys.exit(1)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = args.input_file.stem
    output_file = args.output_dir / f"{stem}.md"
    meta_file = args.output_dir / f"{stem}.meta.json"

    input_hash = _hash_file(args.input_file)

    if _should_skip(output_file, meta_file, input_hash, args.force):
        print(
            f"Skipping: {output_file} already exists with matching hash",
            file=sys.stderr,
        )
        sys.exit(0)

    provider = ClaudeCodeProvider()
    page_count = get_page_count(args.input_file)
    print(f"Processing: {args.input_file} ({page_count} pages)", file=sys.stderr)

    all_meta = []

    if page_count <= MAX_PAGES_SINGLE_PASS:
        try:
            content, meta = _extract_with_retry(provider, args.input_file)
            all_meta.append(meta)
        except RuntimeError:
            print(
                "Single-pass failed after retries, falling back to chunked extraction",
                file=sys.stderr,
            )
            content, chunk_metas = _extract_chunked(
                provider, args.input_file, CHUNK_SIZE
            )
            all_meta.extend(chunk_metas)
    else:
        content, chunk_metas = _extract_chunked(provider, args.input_file, CHUNK_SIZE)
        all_meta.extend(chunk_metas)

    content = _patch_frontmatter(content, input_hash, page_count)

    # Validate and auto-fix
    validation = validate(content)
    if validation.fixed:
        content = validation.fixed
    for warning in validation.warnings:
        print(f"Validation warning: {warning}", file=sys.stderr)
    for error in validation.errors:
        print(f"Validation error: {error}", file=sys.stderr)

    output_file.write_text(content)
    print(f"Written: {output_file}", file=sys.stderr)

    # Save metadata
    total_cost = sum(m.get("cost_usd", 0) for m in all_meta)
    total_duration = sum(m.get("duration_ms", 0) for m in all_meta)
    combined_meta = {
        "input_hash": input_hash,
        "input_filename": args.input_file.name,
        "pages": page_count,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "cost_usd": total_cost,
        "duration_ms": total_duration,
        "chunks": len(all_meta),
        "chunk_details": all_meta,
    }
    meta_file.write_text(json.dumps(combined_meta, indent=2))
    print(f"Metadata: {meta_file} (cost: ${total_cost:.4f})", file=sys.stderr)


def _extract_chunked(provider, pdf_path: Path, chunk_size: int) -> tuple[str, list]:
    """Extract a PDF in chunks, merging results.

    Each chunk is retried before falling back to smaller chunks.
    The first successful chunk's frontmatter is kept; subsequent chunks
    have their frontmatter stripped before merging.
    """
    chunks = split_pdf(pdf_path, max_pages=chunk_size)
    contents = []
    metas = []

    for i, chunk in enumerate(chunks, 1):
        page_end = chunk["page_offset"] + chunk["page_count"] - 1
        print(
            f"Processing chunk {i}/{len(chunks)}, "
            f"pages {chunk['page_offset']}-{page_end}",
            file=sys.stderr,
        )
        try:
            content, meta = _extract_chunk_with_retry(
                provider,
                chunk["pdf_data"],
                chunk["page_offset"],
                chunk["page_count"],
            )
            contents.append(content)
            metas.append(meta)
        except RuntimeError:
            # Retries exhausted - try smaller chunks
            if chunk_size <= MIN_CHUNK_SIZE:
                print(
                    f"Error: chunk pages {chunk['page_offset']}-{page_end} "
                    f"failed at minimum chunk size ({MIN_CHUNK_SIZE} pages). "
                    f"Skipping this chunk.",
                    file=sys.stderr,
                )
                continue
            smaller = chunk_size // 2
            print(
                f"Chunk {i} failed after retries, re-splitting "
                f"pages {chunk['page_offset']}-{page_end} into {smaller}-page chunks",
                file=sys.stderr,
            )
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(chunk["pdf_data"])
                chunk_path = Path(f.name)
            try:
                sub_content, sub_metas = _extract_chunked(provider, chunk_path, smaller)
                contents.append(sub_content)
                metas.extend(sub_metas)
            finally:
                chunk_path.unlink(missing_ok=True)

    if not contents:
        raise RuntimeError("All chunks failed - no content extracted")

    # Merge: keep the first chunk's frontmatter, strip from the rest
    merged = contents[0]
    for chunk_content in contents[1:]:
        body = _strip_frontmatter(chunk_content)
        merged += "\n" + body

    return merged, metas


if __name__ == "__main__":
    main()
