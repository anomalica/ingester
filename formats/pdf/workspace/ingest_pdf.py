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
from shared.pipeline_version import current_version
from shared.record import (
    clean_title,
    get_version,
    normalise_classification,
    write_record,
)
from shared.validator import strip_code_fences, validate
from shared.verification import build_sidecar, needs_sidecar, write_sidecar

# Claude Code limits (multi-turn overhead makes large documents slow)
CLAUDE_CODE_MAX_PAGES_SINGLE_PASS = 20
CLAUDE_CODE_CHUNK_SIZE = 20
# API limits (model may stop early on very long documents)
API_MAX_PAGES_SINGLE_PASS = 50
API_CHUNK_SIZE = 50
MIN_CHUNK_SIZE = 1
MAX_RETRIES = 2

OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent / "ingests"


def default_copyright(manifest: dict) -> dict | None:
    """The copyright block for a PDF when no existing record and no explicit
    --copyright status apply, or None to leave _patch_frontmatter's `restricted`.

    A source FETCHED from a public URL is, by the fact that we retrieved it
    anonymously, publicly accessible - so it defaults to `publicly_accessible`,
    matching the audio/video/web handlers (which all default a URL fetch that way).
    Only a local file of UNKNOWN provenance keeps the conservative `restricted`.
    This does NOT open the original PDF: `publicly_accessible` still gates the source
    file behind proof-of-possession; it only lets the extracted TEXT be surfaced, as
    for any web/AV source.
    """
    if manifest.get("copyright_status"):
        return {"status": manifest["copyright_status"]}
    if str(manifest.get("source", "")).startswith(("http://", "https://")):
        return {"status": "publicly_accessible"}
    return None


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
    source_url: str | None = None,
    source_id: str | None = None,
    fetched_url: str | None = None,
    source_file: str | None = None,
) -> str:
    """Inject content_hash, provenance, processing block, and fix page count in the YAML frontmatter."""
    parts = content.split("---", 2)
    if len(parts) < 3:
        return content

    frontmatter = parts[1]

    # Ensure title is always quoted, and strip leaked placeholder words
    # ("undefined", "null", "None") the model sometimes emits for a missing
    # title field - these otherwise propagate into the knowledge graph.
    title_match = re.search(r'^title: (?!")(.+)$', frontmatter, re.MULTILINE)
    if title_match:
        raw_title = title_match.group(1)
        escaped = clean_title(raw_title).replace('"', '\\"')
        frontmatter = frontmatter.replace(f"title: {raw_title}", f'title: "{escaped}"')
    else:
        quoted_match = re.search(r'^title: "(.*)"$', frontmatter, re.MULTILINE)
        if quoted_match:
            raw_title = quoted_match.group(1)
            cleaned = clean_title(raw_title.replace('\\"', '"')).replace('"', '\\"')
            if cleaned != raw_title:
                frontmatter = frontmatter.replace(
                    f'title: "{raw_title}"', f'title: "{cleaned}"'
                )

    # Provenance the extraction model cannot know (it never sees the URL). The
    # acquire manifest carries these; without them ./ingest's URL dedup cannot
    # recognise an already-ingested PDF and would re-acquire and re-extract.
    for key, value in (
        ("source_url", source_url),
        ("source_id", source_id),
        ("fetched_url", fetched_url),
        ("source_file", source_file),
    ):
        if value and f"\n{key}:" not in f"\n{frontmatter}":
            frontmatter = frontmatter.rstrip("\n") + f"\n{key}: {value}\n"

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
        processing += f"\n  pipeline_version: {current_version('pdf')}"
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


# Metered pricing, USD per 1M tokens (input, output). Used only to report an
# estimated cost for API runs; the subscription path spends no dollars and its
# meta carries no token counts.
_MODEL_PRICING = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def _report_usage(all_meta: list[dict], model: str) -> None:
    """Print token usage and an estimated cost for a metered extraction run.
    No-op when the meta carries no token counts (the subscription path)."""
    total_in = sum((m or {}).get("input_tokens", 0) for m in all_meta)
    total_out = sum((m or {}).get("output_tokens", 0) for m in all_meta)
    cache_read = sum((m or {}).get("cache_read_input_tokens", 0) for m in all_meta)
    if not total_in and not total_out:
        return
    in_price, out_price = _MODEL_PRICING.get(model, (0.0, 0.0))
    cost = (total_in * in_price + total_out * out_price) / 1_000_000
    cache_note = f", {cache_read:,} cache-read" if cache_read else ""
    est = f" = ~${cost:.2f}" if (in_price or out_price) else ""
    print(
        f"Usage ({model}): {total_in:,} input + {total_out:,} output tokens"
        f"{cache_note}{est}",
        file=sys.stderr,
    )


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
    manifest_copyright = None
    source_url = None
    source_id = None
    fetched_url = None
    if args.staging_dir:
        import json

        manifest_path = args.staging_dir / "manifest.json"
        if not manifest_path.exists():
            print(f"Error: no manifest.json in {args.staging_dir}", file=sys.stderr)
            sys.exit(1)
        manifest = json.loads(manifest_path.read_text())
        args.input_file = args.staging_dir / manifest["asset"]
        source_url = manifest.get("source")
        source_id = manifest.get("source_id")
        # Only distinct fetch URLs are worth recording (acquire sets it equal
        # to source for a plain fetch).
        manifest_fetched = manifest.get("fetched_url")
        if manifest_fetched and manifest_fetched != source_url:
            fetched_url = manifest_fetched
        manifest_copyright = default_copyright(manifest)

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

    # A PDF ingested from a local file has no URL; record its original filename
    # as provenance so the record is not left with no acquisition origin.
    source_file = args.input_file.name if not source_url else None

    store_dir = output_dir / "store"
    records_dir = output_dir / "records"
    store_dir.mkdir(parents=True, exist_ok=True)
    records_dir.mkdir(parents=True, exist_ok=True)

    input_hash = hash_file(args.input_file)

    if not args.force and store_exists(store_dir, input_hash):
        print(f"Skipping: {input_hash}.md already exists in store", file=sys.stderr)
        sys.exit(0)

    # Copyright precedence: an existing record's block (preserved on
    # --force re-ingest) wins, otherwise the status declared in the
    # manifest (--copyright on the host script), otherwise the
    # _patch_frontmatter default of restricted.
    existing_copyright = manifest_copyright
    existing_record = store_dir / f"{input_hash}.md"
    if existing_record.exists():
        existing_fm = _extract_frontmatter(existing_record.read_text())
        if existing_fm and "copyright" in existing_fm:
            existing_copyright = existing_fm["copyright"]

    # Provider selection is opt-in to the metered API: default to the
    # Claude Code session (flat-rate subscription) and only use the
    # Anthropic API when INGEST_USE_API=1 is explicitly set (via the
    # host script's --api flag). Previously mere presence of
    # ANTHROPIC_API_KEY silently routed everything through the metered
    # API, which made bulk runs unexpectedly expensive.
    using_api = os.environ.get("INGEST_USE_API") == "1"
    if using_api and not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "INGEST_USE_API=1 but ANTHROPIC_API_KEY is unset; "
            "falling back to Claude Code",
            file=sys.stderr,
        )
        using_api = False
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
        print(
            "Using Claude Code session (default; pass --api for metered API)",
            file=sys.stderr,
        )

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
    _report_usage(all_meta, model_name)
    provider_name = "anthropic-api" if using_api else "claude-code"
    content = _patch_frontmatter(
        content,
        input_hash,
        page_count,
        model=model_name,
        provider=provider_name,
        chunks=len(all_meta),
        existing_copyright=existing_copyright,
        source_url=source_url,
        source_id=source_id,
        fetched_url=fetched_url,
        source_file=source_file,
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
                source_url=source_url,
                source_id=source_id,
                fetched_url=fetched_url,
                source_file=source_file,
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

    # Reconcile classification markings: lift the document banner to a
    # frontmatter `classification` field, drop redundant in-body repeats,
    # and convert differing portion markings to inline {{classification}}.
    content = normalise_classification(content)

    # Extract frontmatter for symlink naming
    fm = _extract_frontmatter(content)
    raw_date = fm.get("date_published", fm.get("date")) if fm else None
    # A genuinely-null date_published (YAML null) must not become the string
    # "None" in the symlink name - fall back to "undated".
    date = str(raw_date) if raw_date else "undated"
    source_type = fm.get("source_type", "pdf") if fm else "pdf"
    title = clean_title(fm.get("title", "untitled")) if fm else "untitled"

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

    if needs_sidecar(content):
        sidecar = build_sidecar(
            content, source_path=args.input_file, page_count=page_count
        )
        sidecar_path = write_sidecar(store_dir, input_hash, sidecar)
        print(
            f"Verification: {sidecar_path.name} ({len(sidecar.get('challenges', []))} challenges)",
            file=sys.stderr,
        )


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
