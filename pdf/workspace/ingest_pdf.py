#!/usr/bin/env python3
"""PDF ingester - extracts structured content from PDFs into Anomalica record format."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from extraction.chunker import get_page_count, split_pdf
from extraction.claude_code import ClaudeCodeProvider

MAX_PAGES_SINGLE_PASS = 100
CHUNK_SIZE = 50
MIN_CHUNK_SIZE = 5


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
    except (json.JSONDecodeError, KeyError):
        return False


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
            content, meta = provider.extract(args.input_file)
            all_meta.append(meta)
        except RuntimeError:
            print(
                "Single-pass failed, falling back to chunked extraction",
                file=sys.stderr,
            )
            content, chunk_metas = _extract_chunked(
                provider, args.input_file, CHUNK_SIZE
            )
            all_meta.extend(chunk_metas)
    else:
        content, chunk_metas = _extract_chunked(provider, args.input_file, CHUNK_SIZE)
        all_meta.extend(chunk_metas)

    # Inject content_hash into frontmatter if not already present
    if "content_hash:" not in content:
        content = content.replace(
            "source_type: pdf",
            f"source_type: pdf\ncontent_hash: sha256:{input_hash}",
        )

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
            content, meta = provider.extract_chunk(
                chunk["pdf_data"], chunk["page_offset"], chunk["page_count"]
            )
            contents.append(content)
            metas.append(meta)
        except RuntimeError:
            if chunk_size <= MIN_CHUNK_SIZE:
                print(
                    f"Error: chunk starting at page {chunk['page_offset']} "
                    f"failed at minimum chunk size ({MIN_CHUNK_SIZE} pages). "
                    f"Flagging for manual intervention.",
                    file=sys.stderr,
                )
                sys.exit(1)
            smaller = chunk_size // 2
            print(
                f"Chunk {i} failed, re-splitting pages {chunk['page_offset']}-{page_end} "
                f"into {smaller}-page chunks",
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

    # First chunk has the frontmatter. Subsequent chunks just have content.
    # Join them, stripping any duplicate frontmatter from later chunks.
    if len(contents) == 1:
        return contents[0], metas

    merged = contents[0]
    for chunk_content in contents[1:]:
        # Strip frontmatter from subsequent chunks if present
        if chunk_content.startswith("---"):
            parts = chunk_content.split("---", 2)
            if len(parts) >= 3:
                chunk_content = parts[2]
        merged += "\n" + chunk_content

    return merged, metas


if __name__ == "__main__":
    main()
