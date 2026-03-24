"""Tests for the ingest_pdf CLI and pipeline."""

import json
import subprocess
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def test_cli_missing_input_file():
    result = subprocess.run(
        ["python", "workspace/ingest_pdf.py"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


def test_cli_nonexistent_file():
    result = subprocess.run(
        ["python", "workspace/ingest_pdf.py", "/nonexistent.pdf"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "not found" in result.stderr.lower() or "error" in result.stderr.lower()


def test_cli_skips_existing_output_with_matching_hash(tmp_path):
    """When output .md and .meta.json exist with matching hash, skip."""
    import hashlib

    pdf_path = FIXTURES / "simple.pdf"
    pdf_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()

    output_md = tmp_path / "simple.md"
    output_md.write_text("existing content")
    meta_file = tmp_path / "simple.meta.json"
    meta_file.write_text(json.dumps({"input_hash": pdf_hash}))

    result = subprocess.run(
        ["python", "workspace/ingest_pdf.py", str(pdf_path), str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "skip" in result.stderr.lower()
    assert output_md.read_text() == "existing content"


def test_cli_reprocesses_when_hash_mismatches(tmp_path):
    """When output exists but hash differs, re-extract."""
    output_md = tmp_path / "simple.md"
    output_md.write_text("old content")
    meta_file = tmp_path / "simple.meta.json"
    meta_file.write_text(json.dumps({"input_hash": "wrong_hash"}))

    # This will fail because Claude Code isn't available in tests,
    # but it should NOT skip - it should attempt extraction.
    result = subprocess.run(
        [
            "python",
            "workspace/ingest_pdf.py",
            str(FIXTURES / "simple.pdf"),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    # It will fail (no claude), but it should have tried (not skipped)
    assert "skip" not in result.stderr.lower()
    assert "hash mismatch" in result.stderr.lower()
