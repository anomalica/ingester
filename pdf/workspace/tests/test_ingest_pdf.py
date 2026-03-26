"""Tests for the ingest_pdf CLI and pipeline."""

import hashlib
import subprocess
from pathlib import Path

from shared.hashing import store_exists
from shared.record import slugify, symlink_name

FIXTURES = Path(__file__).parent / "fixtures"


def test_cli_nonexistent_file():
    result = subprocess.run(
        ["python", "workspace/ingest_pdf.py", "/nonexistent.pdf"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "not found" in result.stderr.lower() or "error" in result.stderr.lower()


def test_store_exists_when_hash_file_present(tmp_path):
    pdf_path = FIXTURES / "simple.pdf"
    pdf_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    (tmp_path / f"{pdf_hash}.md").write_text("existing")
    assert store_exists(tmp_path, pdf_hash)


def test_store_not_exists_when_missing(tmp_path):
    assert not store_exists(tmp_path, "nonexistent_hash")


def test_slugify():
    assert slugify("David Fravor Statement") == "david-fravor-statement"
    assert slugify("Report: Volume 1") == "report-volume-1"
    assert slugify("  Multiple   Spaces  ") == "multiple-spaces"


def test_symlink_name():
    name = symlink_name("2023-07-26", "pdf", "David Fravor Statement")
    assert name == "2023-07-26-pdf-david-fravor-statement.md"
