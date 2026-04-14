#!/usr/bin/env python3
"""PDF ingester - extracts structured content from PDFs into Anomalica record format."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from extraction.chunker import extract_page, get_page_count, split_pdf
from shared.hashing import content_hash_label, hash_file, store_exists
from shared.record import get_version, write_record
from shared.validator import strip_code_fences, validate

# Claude Code limits (multi-turn overhead makes large documents slow)
CLAUDE_CODE_MAX_PAGES_SINGLE_PASS = 20
CLAUDE_CODE_CHUNK_SIZE = 20
# API limits (model may stop early on very long documents)
API_MAX_PAGES_SINGLE_PASS = 50
API_CHUNK_SIZE = 50
MIN_CHUNK_SIZE = 1
MAX_RETRIES = 2

OUTPUT_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent.parent / "anomalica-ingests"
)


def _check_record(content: str, min_chars: int = 500) -> tuple[bool, str]:
    """Check that content looks like a valid record. Returns (valid, reason)."""
    stripped = strip_code_fences(content)
    if not stripped.startswith("---"):
        return False, "no frontmatter (doesn't start with ---)"
    if len(stripped) < min_chars:
        return False, f"content too short (expected at least {min_chars} chars)"
    return True, ""


def _clean_annotations(content: str) -> str:
    """Remove blank lines before --> in annotation comment blocks."""
    return re.sub(r"\n\n(-->)", r"\n\1", content)


def _patch_frontmatter(
    content: str,
    input_hash: str,
    page_count: int,
    model: str | None = None,
    provider: str | None = None,
    chunks: int | None = None,
    existing_copyright: dict | None = None,
) -> str:
    """Inject content_hash, processing block, and fix page count in the YAML frontmatter."""
    parts = content.split("---", 2)
    if len(parts) < 3:
        return content

    frontmatter = parts[1]

    # Ensure title is always quoted
    title_match = re.search(r'^title: (?!")(.+)$', frontmatter, re.MULTILINE)
    if title_match:
        raw_title = title_match.group(1)
        escaped = raw_title.replace('"', '\\"')
        frontmatter = frontmatter.replace(f"title: {raw_title}", f'title: "{escaped}"')

    if "content_hash:" not in frontmatter:
        frontmatter = (
            frontmatter.rstrip("\n")
            + f"\ncontent_hash: {content_hash_label(input_hash)}\n"
        )

    frontmatter = re.sub(r"pages: \d+", f"pages: {page_count}", frontmatter, count=1)

    # Rename date to date_published if present
    if "\ndate:" in frontmatter and "\ndate_published:" not in frontmatter:
        frontmatter = re.sub(r"\ndate: ", "\ndate_published: ", frontmatter, count=1)

    if "date_extracted:" not in frontmatter:
        frontmatter = (
            frontmatter.rstrip("\n")
            + f"\ndate_extracted: {datetime.now(timezone.utc).isoformat()}\n"
        )

    if "copyright:" not in frontmatter:
        if existing_copyright and isinstance(existing_copyright, dict):
            import yaml

            copyright_yaml = yaml.dump(
                {"copyright": existing_copyright}, default_flow_style=False
            ).rstrip("\n")
            frontmatter = frontmatter.rstrip("\n") + "\n" + copyright_yaml + "\n"
        else:
            frontmatter = (
                frontmatter.rstrip("\n") + "\ncopyright:\n  status: restricted\n"
            )

    if "processing:" not in frontmatter:
        sdk_version = "unknown"
        try:
            import anthropic

            sdk_version = anthropic.__version__
        except (ImportError, AttributeError):
            pass
        processing = "\nprocessing:"
        processing += "\n  handler: pdf"
        processing += f"\n  version: {get_version()}"
        processing += "\n  tools:"
        processing += "\n    - name: claude"
        processing += f'\n      version: "{model or "unknown"}"'
        processing += "\n      role: extraction"
        processing += f"\n      provider: {provider or 'unknown'}"
        processing += f'\n      sdk_version: "{sdk_version}"'
        if chunks and chunks > 1:
            processing += f"\n  chunks: {chunks}"
        frontmatter = frontmatter.rstrip("\n") + processing + "\n"

    return f"---{frontmatter}---{parts[2]}"


def _renumber_pages(content: str, offset: int) -> str:
    """Add offset to all file_page annotations in content."""
    if offset == 0:
        return content

    def _add_offset(match):
        page_num = int(match.group(1))
        return f"file_page: {page_num + offset}"

    return re.sub(r"file_page: (\d+)", _add_offset, content)


def _find_missing_pages(content: str, expected_pages: int) -> list[int]:
    """Find page numbers that should be in the output but aren't."""
    found = set(int(m) for m in re.findall(r"file_page: (\d+)", content))
    return sorted(set(range(1, expected_pages + 1)) - found)


def _repair_missing_pages(
    content: str,
    missing_pages: list[int],
    provider,
    pdf_path: Path,
    max_attempts: int = 3,
) -> str:
    """Extract and insert missing pages into existing content."""
    for page_num in missing_pages:
        print(f"Repairing missing page {page_num}", file=sys.stderr)
        page_data = extract_page(pdf_path, page_num)
        for attempt in range(max_attempts):
            try:
                page_content, _ = provider.extract_chunk(page_data, page_num, 1)
                page_body = _strip_frontmatter(page_content)
                page_body = _renumber_pages(page_body, page_num - 1)

                next_page = page_num + 1
                insertion_point = content.find(f"file_page: {next_page}")
                if insertion_point >= 0:
                    block_start = content.rfind("<!--\n", 0, insertion_point)
                    if block_start < 0:
                        block_start = content.rfind(
                            "<!-- file_page:", 0, insertion_point
                        )
                    if block_start >= 0:
                        content = (
                            content[:block_start]
                            + "\n"
                            + page_body
                            + content[block_start:]
                        )
                    else:
                        content = (
                            content[:insertion_point]
                            + "\n"
                            + page_body
                            + content[insertion_point:]
                        )
                else:
                    content += "\n" + page_body

                print(f"  Page {page_num} repaired", file=sys.stderr)
                break
            except RuntimeError as e:
                print(
                    f"  Page {page_num} attempt {attempt + 1} failed: {e}",
                    file=sys.stderr,
                )
                if attempt == max_attempts - 1:
                    print(f"  Giving up on page {page_num}", file=sys.stderr)

    return content


def _strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter from content, keeping only the body."""
    stripped = content.strip()
    if not stripped.startswith("---"):
        return content
    parts = stripped.split("---", 2)
    if len(parts) >= 3:
        return parts[2]
    return content


def _extract_frontmatter(content: str) -> dict | None:
    """Parse the YAML frontmatter from content."""
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        import yaml

        fm = yaml.safe_load(parts[1])
        return fm if isinstance(fm, dict) else None
    except Exception:
        return None


def _extract_with_retry(provider, pdf_path: Path) -> tuple[str, dict]:
    """Extract with retries. Validates output is a real record."""
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
        except RuntimeError as e:
            print(f"Attempt {attempt + 1} failed: {e}, retrying", file=sys.stderr)
            last_error = e
    raise last_error


def _extract_chunk_with_retry(
    provider, pdf_data: bytes, page_offset: int, page_count: int
) -> tuple[str, dict]:
    """Extract a chunk with retries. Validates content is proportional to page count."""
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
    parser.add_argument(
        "--force", action="store_true", help="Re-process even if output file exists"
    )
    parser.add_argument(
        "--staging-dir",
        type=Path,
        help="Path to staging directory (alternative to input_file)",
    )
    args = parser.parse_args()

    # If staging dir provided, read the asset path from the manifest
    if args.staging_dir:
        import json

        manifest_path = args.staging_dir / "manifest.json"
        if not manifest_path.exists():
            print(f"Error: no manifest.json in {args.staging_dir}", file=sys.stderr)
            sys.exit(1)
        manifest = json.loads(manifest_path.read_text())
        args.input_file = args.staging_dir / manifest["asset"]

    # Auto-detect mount paths from container-magic
    mnt_input = Path("/mnt/input")
    mnt_output = Path("/mnt/output")
    if not args.input_file and mnt_input.exists():
        pdfs = list(mnt_input.glob("*.pdf"))
        if pdfs:
            args.input_file = pdfs[0]

    output_dir = mnt_output if mnt_output.exists() else OUTPUT_DIR

    if not args.input_file:
        parser.error("input_file is required (or use cm run ingest input=<file>)")

    if not args.input_file.exists():
        print(f"Error: file not found: {args.input_file}", file=sys.stderr)
        sys.exit(1)

    store_dir = output_dir / "store"
    records_dir = output_dir / "records"
    store_dir.mkdir(parents=True, exist_ok=True)
    records_dir.mkdir(parents=True, exist_ok=True)

    input_hash = hash_file(args.input_file)

    if not args.force and store_exists(store_dir, input_hash):
        print(f"Skipping: {input_hash}.md already exists in store", file=sys.stderr)
        sys.exit(0)

    # Preserve existing copyright block when re-ingesting with --force
    existing_copyright = None
    existing_record = store_dir / f"{input_hash}.md"
    if existing_record.exists():
        existing_fm = _extract_frontmatter(existing_record.read_text())
        if existing_fm and "copyright" in existing_fm:
            existing_copyright = existing_fm["copyright"]

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

    if page_count <= max_single_pass:
        try:
            content, meta = _extract_with_retry(provider, args.input_file)
            all_meta.append(meta)
        except RuntimeError:
            print(
                "Single-pass failed, falling back to chunked extraction",
                file=sys.stderr,
            )
            content, chunk_metas = _extract_chunked(
                provider, args.input_file, chunk_size
            )
            all_meta.extend(chunk_metas)
    else:
        content, chunk_metas = _extract_chunked(provider, args.input_file, chunk_size)
        all_meta.extend(chunk_metas)

    content = _clean_annotations(content)
    model_name = getattr(provider, "model", "unknown")
    provider_name = "anthropic-api" if using_api else "claude-code"
    content = _patch_frontmatter(
        content,
        input_hash,
        page_count,
        model=model_name,
        provider=provider_name,
        chunks=len(all_meta),
        existing_copyright=existing_copyright,
    )

    # Validate, auto-fix, and repair missing pages
    repair_provider = provider

    validation = validate(content)
    if validation.fixed:
        content = validation.fixed
    for warning in validation.warnings:
        print(f"Validation warning: {warning}", file=sys.stderr)
    for error in validation.errors:
        print(f"Validation error: {error}", file=sys.stderr)

    # PDF-specific: check page completeness
    found_pages = set(int(m) for m in re.findall(r"file_page: (\d+)", content))
    if found_pages:
        max_page = max(found_pages)
        missing_count = page_count - max_page
        if missing_count > page_count * 0.25:
            validation.errors.append(
                f"Output truncated: only {max_page} of {page_count} pages extracted"
            )
            print(
                f"Validation error: Output truncated: only {max_page} of {page_count} pages",
                file=sys.stderr,
            )

    # If pages are missing, try to repair them individually
    if validation.errors:
        missing = _find_missing_pages(content, page_count)
        if missing:
            print(f"Attempting to repair {len(missing)} missing pages", file=sys.stderr)
            content = _repair_missing_pages(
                content, missing, repair_provider, args.input_file
            )
            content = _patch_frontmatter(
                content,
                input_hash,
                page_count,
                model=model_name,
                provider=provider_name,
                chunks=len(all_meta),
                existing_copyright=existing_copyright,
            )

            validation = validate(content)
            if validation.fixed:
                content = validation.fixed
            for warning in validation.warnings:
                print(f"Validation warning: {warning}", file=sys.stderr)
            for error in validation.errors:
                print(f"Validation error: {error}", file=sys.stderr)
            if not validation.errors:
                print("Repair successful", file=sys.stderr)

    # Extract frontmatter for symlink naming
    fm = _extract_frontmatter(content)
    date = str(fm.get("date_published", fm.get("date", "undated"))) if fm else "undated"
    source_type = fm.get("source_type", "pdf") if fm else "pdf"
    title = fm.get("title", "untitled") if fm else "untitled"

    # Write to store and create symlink
    record_path, symlink_path = write_record(
        store_dir=store_dir,
        records_dir=records_dir,
        hex_hash=input_hash,
        content=content,
        date=date,
        source_type=source_type,
        title=title,
    )
    print(f"Written: {record_path}", file=sys.stderr)
    print(f"Symlink: {symlink_path}", file=sys.stderr)


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
    chunk_results = []
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
            chunk_results.append((content, real_offset))
            metas.append(meta)
        except RuntimeError:
            if chunk_size <= MIN_CHUNK_SIZE:
                print(
                    f"API failed for page {real_offset}, trying Claude Code",
                    file=sys.stderr,
                )
                try:
                    from extraction.claude_code import ClaudeCodeProvider

                    cc = ClaudeCodeProvider()
                    content, meta = cc.extract_chunk(
                        chunk["pdf_data"], real_offset, chunk["page_count"]
                    )
                    chunk_results.append((content, real_offset))
                    metas.append(meta)
                except RuntimeError as e:
                    print(
                        f"Claude Code also failed for page {real_offset}: {e}. Skipping.",
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
                chunk_results.append((sub_content, real_offset))
                metas.extend(sub_metas)
            finally:
                chunk_path.unlink(missing_ok=True)

    if not chunk_results:
        raise RuntimeError("All chunks failed - no content extracted")

    first_content, first_offset = chunk_results[0]
    merged = _renumber_pages(first_content, first_offset - 1)
    for chunk_content, real_offset in chunk_results[1:]:
        body = _strip_frontmatter(chunk_content)
        body = _renumber_pages(body, real_offset - 1)
        merged += "\n" + body

    return merged, metas


if __name__ == "__main__":
    main()
