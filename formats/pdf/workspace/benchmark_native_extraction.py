#!/usr/bin/env python3
"""Benchmark offline native PDF extraction against a page-anchored reviewed ingest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import time
import unicodedata
from collections import Counter
from pathlib import Path

import pymupdf

PAGE_MARKER = re.compile(r"<!--\s*file_page:\s*(\d+)\s*-->")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
TOKEN = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)
MIN_PAGES = 40
MAX_PAGES = 60
PRODUCTION_DECISION = {
    "code": "supplement-only",
    "summary": "Use native text extraction as a supplement; do not replace AI transcription.",
    "role": "supplement",
    "replace_ai_transcription": False,
}


def reviewed_pages(record: str) -> dict[int, str]:
    """Split a reviewed ingest body on its file-page annotations."""
    parts = record.split("---", 2)
    body = parts[2] if len(parts) == 3 else record
    markers = list(PAGE_MARKER.finditer(body))
    pages: dict[int, str] = {}
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(body)
        pages[int(marker.group(1))] = COMMENT.sub(" ", body[marker.end() : end])
    return pages


def words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).casefold()
    return [match.group(0).replace("’", "'") for match in TOKEN.finditer(text)]


def edit_distance(reference: list[str], candidate: list[str]) -> int:
    """Levenshtein distance using memory proportional to the shorter sequence."""
    if len(reference) < len(candidate):
        reference, candidate = candidate, reference
    previous = list(range(len(candidate) + 1))
    for row, reference_word in enumerate(reference, 1):
        current = [row]
        for column, candidate_word in enumerate(candidate, 1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (reference_word != candidate_word),
                )
            )
        previous = current
    return previous[-1]


def score_page(reference: str, candidate: str) -> dict:
    reference_words = words(reference)
    candidate_words = words(candidate)
    overlap = sum((Counter(reference_words) & Counter(candidate_words)).values())
    errors = edit_distance(reference_words, candidate_words)
    return {
        "reference_words": len(reference_words),
        "candidate_words": len(candidate_words),
        "word_errors": errors,
        "word_error_pct": round(errors / max(len(reference_words), 1) * 100, 2),
        "word_precision_pct": round(overlap / max(len(candidate_words), 1) * 100, 2),
        "word_recall_pct": round(overlap / max(len(reference_words), 1) * 100, 2),
    }


def extract_pages(pdf_path: Path) -> list[str]:
    with pymupdf.open(pdf_path) as document:
        return [page.get_text("text", sort=True) for page in document]


def benchmark(pdf_path: Path, reviewed_path: Path) -> dict:
    started = time.monotonic()
    candidates = extract_pages(pdf_path)
    page_count = len(candidates)
    if not MIN_PAGES <= page_count <= MAX_PAGES:
        raise ValueError(
            f"pilot requires {MIN_PAGES}-{MAX_PAGES} pages; input has {page_count}"
        )

    references = reviewed_pages(reviewed_path.read_text(encoding="utf-8"))
    expected = set(range(1, page_count + 1))
    missing = sorted(expected - references.keys())
    extra = sorted(references.keys() - expected)
    if missing or extra:
        raise ValueError(
            f"reviewed page anchors do not match PDF: missing={missing}, extra={extra}"
        )

    pages = []
    total_reference = total_candidate = total_errors = total_overlap = 0
    for page_number, candidate in enumerate(candidates, 1):
        reference = references[page_number]
        score = score_page(reference, candidate)
        reference_tokens = words(reference)
        candidate_tokens = words(candidate)
        overlap = sum((Counter(reference_tokens) & Counter(candidate_tokens)).values())
        total_reference += score["reference_words"]
        total_candidate += score["candidate_words"]
        total_errors += score["word_errors"]
        total_overlap += overlap
        pages.append({"file_page": page_number, **score})

    return {
        "inputs": {
            "pdf_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
            "reviewed_ingest_sha256": hashlib.sha256(
                reviewed_path.read_bytes()
            ).hexdigest(),
        },
        "method": "pymupdf-native-text-geometric-sort",
        "pymupdf_version": pymupdf.VersionBind,
        "page_count": page_count,
        "bounds": {"minimum_pages": MIN_PAGES, "maximum_pages": MAX_PAGES},
        "network_calls": 0,
        "model_downloads": 0,
        "ocr_performed": False,
        "production_decision": PRODUCTION_DECISION,
        "limitations": [
            "The source has an embedded text layer, so this pilot does not measure raster OCR.",
            "The reviewed ingest is roughly checked, not character-perfect ground truth.",
            "Markdown and annotations are excluded before word comparison.",
        ],
        "aggregate": {
            "reference_words": total_reference,
            "candidate_words": total_candidate,
            "word_errors": total_errors,
            "word_error_pct": round(total_errors / max(total_reference, 1) * 100, 2),
            "word_precision_pct": round(
                total_overlap / max(total_candidate, 1) * 100, 2
            ),
            "word_recall_pct": round(total_overlap / max(total_reference, 1) * 100, 2),
            "pages_with_candidate_text": sum(bool(words(text)) for text in candidates),
        },
        "pages": pages,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record_id", help="SHA-256 record identity")
    parser.add_argument("--records-dir", type=Path, default=Path("/mnt/records"))
    parser.add_argument("--ingests-dir", type=Path, default=Path("/mnt/output/store"))
    parser.add_argument(
        "--output-dir", type=Path, default=Path("/mnt/benchmark/native")
    )
    args = parser.parse_args()

    if not re.fullmatch(r"[0-9a-f]{64}", args.record_id):
        parser.error("record_id must be a lowercase SHA-256 digest")
    report = benchmark(
        args.records_dir / f"{args.record_id}.pdf",
        args.ingests_dir / f"{args.record_id}.md",
    )
    report["record_id"] = args.record_id
    output = args.output_dir / f"{args.record_id}.json"
    atomic_write_json(output, report)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
