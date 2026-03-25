#!/usr/bin/env python3
"""PDF ingester - extracts structured content from PDFs into Anomalica record format."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from extraction import strip_code_fences
from extraction.chunker import get_page_count, split_pdf
from validator import validate

# Claude Code limits (multi-turn overhead makes large documents slow)
CLAUDE_CODE_MAX_PAGES_SINGLE_PASS = 20
CLAUDE_CODE_CHUNK_SIZE = 20
# API limits (model may stop early on very long documents)
API_MAX_PAGES_SINGLE_PASS = 50
API_CHUNK_SIZE = 50
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


def _check_record(content: str, min_chars: int = 500) -> tuple[bool, str]:
    """Check that content looks like a valid record. Returns (valid, reason)."""
    stripped = strip_code_fences(content)
    if not stripped.startswith("---"):
        return False, "no frontmatter (doesn't start with ---)"
    if len(stripped) < min_chars:
        return False, f"content too short (expected at least {min_chars} chars)"
    return True, ""


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
    from extraction.anthropic_api import ContentFilteredError

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            content, meta = provider.extract(pdf_path)
            valid, reason = _check_record(content)
            if valid:
                return content, meta
            print(
                f"Attempt {attempt + 1}: {reason}, retrying",
                file=sys.stderr,
            )
            last_error = RuntimeError(reason)
        except ContentFilteredError:
            raise
        except RuntimeError as e:
            print(f"Attempt {attempt + 1} failed: {e}, retrying", file=sys.stderr)
            last_error = e
    raise last_error


def _extract_chunk_with_retry(
    provider, pdf_data: bytes, page_offset: int, page_count: int
) -> tuple[str, dict]:
    """Extract a chunk with retries. Validates content is proportional to page count."""
    from extraction.anthropic_api import ContentFilteredError

    min_chars = max(500, page_count * 200)
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            content, meta = provider.extract_chunk(pdf_data, page_offset, page_count)
            valid, reason = _check_record(content, min_chars=min_chars)
            if valid:
                return content, meta
            print(
                f"Chunk attempt {attempt + 1}: {reason} "
                f"({len(content.strip())} chars for {page_count} pages), retrying",
                file=sys.stderr,
            )
            last_error = RuntimeError(reason)
        except ContentFilteredError:
            raise
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

    using_api = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if using_api:
        from extraction.anthropic_api import AnthropicProvider

        provider = AnthropicProvider()
        max_single_pass = API_MAX_PAGES_SINGLE_PASS
        chunk_size = API_CHUNK_SIZE
        print("Using Anthropic API", file=sys.stderr)
    else:
        from extraction.claude_code import ClaudeCodeProvider

        provider = ClaudeCodeProvider()
        max_single_pass = CLAUDE_CODE_MAX_PAGES_SINGLE_PASS
        chunk_size = CLAUDE_CODE_CHUNK_SIZE
        print("Using Claude Code (no ANTHROPIC_API_KEY set)", file=sys.stderr)

    page_count = get_page_count(args.input_file)
    print(f"Processing: {args.input_file} ({page_count} pages)", file=sys.stderr)

    all_meta = []

    try:
        if page_count <= max_single_pass:
            try:
                content, meta = _extract_with_retry(provider, args.input_file)
                all_meta.append(meta)
            except RuntimeError:
                print(
                    "Single-pass failed after retries, falling back to chunked extraction",
                    file=sys.stderr,
                )
                content, chunk_metas = _extract_chunked(
                    provider, args.input_file, chunk_size
                )
                all_meta.extend(chunk_metas)
        else:
            content, chunk_metas = _extract_chunked(
                provider, args.input_file, chunk_size
            )
            all_meta.extend(chunk_metas)
    except Exception as e:
        # Check for content filtering (API only)
        from extraction.anthropic_api import ContentFilteredError

        if isinstance(e, ContentFilteredError) or (
            isinstance(e.__cause__, ContentFilteredError)
        ):
            print(
                "API content filter triggered, falling back to Claude Code",
                file=sys.stderr,
            )
            from extraction.claude_code import ClaudeCodeProvider

            fallback_provider = ClaudeCodeProvider()
            all_meta = []
            if page_count <= max_single_pass:
                content, meta = _extract_with_retry(fallback_provider, args.input_file)
                all_meta.append(meta)
            else:
                content, chunk_metas = _extract_chunked(
                    fallback_provider, args.input_file, CLAUDE_CODE_CHUNK_SIZE
                )
                all_meta.extend(chunk_metas)
        else:
            raise

    content = _patch_frontmatter(content, input_hash, page_count)

    # Validate, auto-fix, and retry if errors found
    max_validation_retries = 2
    for validation_attempt in range(max_validation_retries + 1):
        validation = validate(content)
        if validation.fixed:
            content = validation.fixed
        for warning in validation.warnings:
            print(f"Validation warning: {warning}", file=sys.stderr)

        if not validation.errors:
            break

        for error in validation.errors:
            print(f"Validation error: {error}", file=sys.stderr)

        if validation_attempt < max_validation_retries:
            print(
                f"Re-extracting due to validation errors "
                f"(attempt {validation_attempt + 2})",
                file=sys.stderr,
            )
            all_meta = []
            try:
                if page_count <= max_single_pass:
                    content, meta = _extract_with_retry(provider, args.input_file)
                    all_meta.append(meta)
                else:
                    content, chunk_metas = _extract_chunked(
                        provider, args.input_file, chunk_size
                    )
                    all_meta.extend(chunk_metas)
                content = _patch_frontmatter(content, input_hash, page_count)
            except Exception:
                print("Re-extraction failed", file=sys.stderr)
                break
        else:
            print(
                "Validation errors remain after retries, writing best result",
                file=sys.stderr,
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


def _extract_chunked(
    provider, pdf_path: Path, chunk_size: int, base_offset: int = 0
) -> tuple[str, list]:
    """Extract a PDF in chunks, merging results.

    Each chunk is retried before falling back to smaller chunks.
    The first successful chunk's frontmatter is kept; subsequent chunks
    have their frontmatter stripped before merging.

    base_offset: added to split_pdf's page_offset to get the real page number
    in the original document. Non-zero when recursively re-splitting a failed chunk.
    """
    chunks = split_pdf(pdf_path, max_pages=chunk_size)
    contents = []
    metas = []

    for i, chunk in enumerate(chunks, 1):
        real_offset = chunk["page_offset"] + base_offset
        page_end = real_offset + chunk["page_count"] - 1
        print(
            f"Processing chunk {i}/{len(chunks)}, pages {real_offset}-{page_end}",
            file=sys.stderr,
        )
        try:
            content, meta = _extract_chunk_with_retry(
                provider,
                chunk["pdf_data"],
                real_offset,
                chunk["page_count"],
            )
            contents.append(content)
            metas.append(meta)
        except RuntimeError:
            # Retries exhausted - try smaller chunks
            if chunk_size <= MIN_CHUNK_SIZE:
                print(
                    f"Error: chunk pages {real_offset}-{page_end} "
                    f"failed at minimum chunk size ({MIN_CHUNK_SIZE} pages). "
                    f"Skipping this chunk.",
                    file=sys.stderr,
                )
                continue
            smaller = chunk_size // 2
            print(
                f"Chunk {i} failed after retries, re-splitting "
                f"pages {real_offset}-{page_end} into {smaller}-page chunks",
                file=sys.stderr,
            )
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(chunk["pdf_data"])
                chunk_path = Path(f.name)
            try:
                # split_pdf returns 1-based page_offset, so base_offset is zero-based
                sub_content, sub_metas = _extract_chunked(
                    provider, chunk_path, smaller, base_offset=real_offset - 1
                )
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
