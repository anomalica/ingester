#!/usr/bin/env python3
"""PDF ingester - extracts structured content from PDFs into DoclingDocument JSON."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from extraction.chunker import get_page_count, split_pdf
from extraction.claude_code import ClaudeCodeProvider
from extraction.merger import merge_extraction_results
from output.builder import build_docling_document

MAX_PAGES_SINGLE_PASS = 100
CHUNK_SIZE = 50
MIN_CHUNK_SIZE = 5


def main():
    parser = argparse.ArgumentParser(
        description="Extract content from a PDF into DoclingDocument JSON."
    )
    parser.add_argument(
        "--input", dest="input_file", type=Path, help="Path to the PDF file"
    )
    parser.add_argument(
        "--output",
        dest="output_dir",
        type=Path,
        default=Path("."),
        help="Output directory (default: current directory)",
    )
    parser.add_argument(
        "--force", action="store_true", help="Re-process even if output file exists"
    )
    args = parser.parse_args()

    if not args.input_file:
        parser.error("--input is required")

    if not args.input_file.exists():
        print(f"Error: file not found: {args.input_file}", file=sys.stderr)
        sys.exit(1)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_file = args.output_dir / f"{args.input_file.stem}.json"

    if output_file.exists() and not args.force:
        print(
            f"Skipping: {output_file} already exists (use --force to re-process)",
            file=sys.stderr,
        )
        sys.exit(0)

    provider = ClaudeCodeProvider()
    page_count = get_page_count(args.input_file)
    print(f"Processing: {args.input_file} ({page_count} pages)", file=sys.stderr)

    if page_count <= MAX_PAGES_SINGLE_PASS:
        try:
            result = provider.extract(args.input_file)
            results = [result]
        except RuntimeError:
            print(
                "Single-pass failed, falling back to chunked extraction",
                file=sys.stderr,
            )
            results = _extract_chunked(provider, args.input_file, CHUNK_SIZE)
    else:
        results = _extract_chunked(provider, args.input_file, CHUNK_SIZE)

    merged = merge_extraction_results(results)

    # Validate page coverage
    extracted_pages = set()
    for el in merged.elements:
        extracted_pages.add(el.page)
        if el.page_end:
            for p in range(el.page, el.page_end + 1):
                extracted_pages.add(p)
    missing = set(range(1, page_count + 1)) - extracted_pages
    if missing:
        print(
            f"Warning: pages not referenced in extraction: {sorted(missing)}",
            file=sys.stderr,
        )

    doc = build_docling_document(merged, source_filename=args.input_file.name)
    output_file.write_text(json.dumps(doc.export_to_dict(), indent=2))
    print(f"Written: {output_file}", file=sys.stderr)


def _extract_chunked(provider, pdf_path: Path, chunk_size: int) -> list:
    chunks = split_pdf(pdf_path, max_pages=chunk_size)
    results = []
    for i, chunk in enumerate(chunks, 1):
        page_end = chunk["page_offset"] + chunk["page_count"] - 1
        print(
            f"Processing chunk {i}/{len(chunks)}, "
            f"pages {chunk['page_offset']}-{page_end}",
            file=sys.stderr,
        )
        try:
            result = provider.extract_chunk(
                chunk["pdf_data"], chunk["page_offset"], chunk["page_count"]
            )
            results.append(result)
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
                sub_results = _extract_chunked(provider, chunk_path, smaller)
                for sub_result in sub_results:
                    for el in sub_result.elements:
                        el.page += chunk["page_offset"] - 1
                        if el.page_end:
                            el.page_end += chunk["page_offset"] - 1
                results.extend(sub_results)
            finally:
                chunk_path.unlink(missing_ok=True)
    return results


if __name__ == "__main__":
    main()
