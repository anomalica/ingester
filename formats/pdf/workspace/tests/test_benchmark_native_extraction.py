from pathlib import Path
import hashlib

import pytest

from benchmark_native_extraction import (
    benchmark,
    benchmark_inputs,
    edit_distance,
    reviewed_pages,
    score_page,
)


def test_reviewed_pages_strip_annotations_and_frontmatter():
    record = """---
title: Example
---
<!-- file_page: 1 -->
First page. <!-- image: ignored -->
<!-- file_page: 2 -->
Second page.
"""
    assert reviewed_pages(record) == {1: "\nFirst page.  \n", 2: "\nSecond page.\n"}


def test_score_reports_order_and_vocabulary_separately():
    score = score_page("one two three", "three two one")
    assert score["word_precision_pct"] == 100
    assert score["word_recall_pct"] == 100
    assert score["word_error_pct"] > 0
    assert edit_distance([], ["one"]) == 1


def test_benchmark_enforces_pilot_page_bounds(tmp_path: Path):
    reviewed = tmp_path / "reviewed.md"
    reviewed.write_text("<!-- file_page: 1 -->\nhello", encoding="utf-8")
    fixture = Path(__file__).parent / "fixtures" / "simple.pdf"
    with pytest.raises(ValueError, match="40-60 pages"):
        benchmark(fixture, reviewed)


def test_benchmark_hashes_inputs(monkeypatch, tmp_path: Path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"source")
    reviewed = tmp_path / "reviewed.md"
    reviewed.write_bytes(b"<!-- file_page: 1 -->\nreference")
    monkeypatch.setattr(
        "benchmark_native_extraction.extract_pages", lambda _: ["candidate"] * 40
    )
    reviewed.write_text(
        "".join(f"<!-- file_page: {page} -->\nreference\n" for page in range(1, 41)),
        encoding="utf-8",
    )

    report = benchmark(pdf, reviewed)

    assert report["inputs"] == {
        "pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        "reviewed_ingest_sha256": hashlib.sha256(reviewed.read_bytes()).hexdigest(),
    }
    assert report["production_decision"] == {
        "code": "supplement-only",
        "summary": "Use native text extraction as a supplement; do not replace AI transcription.",
        "role": "supplement",
        "replace_ai_transcription": False,
    }


def test_benchmark_inputs_resolve_record3_pdf_asset(tmp_path: Path):
    records = tmp_path / "records"
    ingests = tmp_path / "ingests"
    records.mkdir()
    ingests.mkdir()
    asset_hash = "a" * 64
    record_hash = "b" * 64
    record = ingests / f"{record_hash}.md"
    record.write_text(
        "---\n"
        "schema: anomalica/record/3\n"
        f"content_hash: sha256:{record_hash}\n"
        "assets:\n"
        f"- asset_hash: sha256:{asset_hash}\n"
        "  source_type: pdf\n"
        "  archived_ext: pdf\n"
        "selection:\n"
        f"- asset_hash: sha256:{asset_hash}\n"
        "  selector:\n"
        "    type: whole\n"
        "---\nbody\n",
        encoding="utf-8",
    )

    assert benchmark_inputs(record_hash, records, ingests) == (
        records / f"{asset_hash}.pdf",
        record,
    )


def test_benchmark_inputs_keep_legacy_pdf_identity(tmp_path: Path):
    records = tmp_path / "records"
    ingests = tmp_path / "ingests"
    records.mkdir()
    ingests.mkdir()
    record_hash = "c" * 64
    record = ingests / f"{record_hash}.md"
    record.write_text("---\nschema: anomalica/record/1\n---\nbody\n")

    assert benchmark_inputs(record_hash, records, ingests) == (
        records / f"{record_hash}.pdf",
        record,
    )
