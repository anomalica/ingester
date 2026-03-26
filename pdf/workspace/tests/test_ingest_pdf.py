"""Tests for the ingest_pdf CLI and pipeline."""

import hashlib
import subprocess
from pathlib import Path

import sys

sys.path.insert(0, "workspace")
from ingest_pdf import _should_skip, _slugify, _build_symlink_name

FIXTURES = Path(__file__).parent / "fixtures"


def test_cli_nonexistent_file():
    result = subprocess.run(
        ["python", "workspace/ingest_pdf.py", "/nonexistent.pdf"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "not found" in result.stderr.lower() or "error" in result.stderr.lower()


def test_should_skip_when_hash_file_exists(tmp_path):
    """When store/{hash}.md exists, skip."""
    pdf_path = FIXTURES / "simple.pdf"
    pdf_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    (tmp_path / f"{pdf_hash}.md").write_text("existing")

    assert _should_skip(tmp_path, pdf_hash, force=False)


def test_should_not_skip_when_missing(tmp_path):
    assert not _should_skip(tmp_path, "nonexistent_hash", force=False)


def test_should_not_skip_when_forced(tmp_path):
    (tmp_path / "somehash.md").write_text("existing")
    assert not _should_skip(tmp_path, "somehash", force=True)


def test_slugify():
    assert _slugify("David Fravor Statement") == "david-fravor-statement"
    assert _slugify("Report: Volume 1") == "report-volume-1"
    assert _slugify("  Multiple   Spaces  ") == "multiple-spaces"
    assert _slugify("Special!@#Characters$%") == "specialcharacters"


def test_build_symlink_name():
    content = """---
schema: anomalica/record/1
title: David Fravor Statement for the House Oversight Committee
date: 2023-07-26
source_type: pdf
---

Content.
"""
    name = _build_symlink_name(content)
    assert (
        name
        == "2023-07-26-pdf-david-fravor-statement-for-the-house-oversight-committee.md"
    )


def test_build_symlink_name_no_frontmatter():
    assert _build_symlink_name("No frontmatter here.") is None


def test_build_symlink_name_missing_fields():
    content = """---
schema: anomalica/record/1
---

Content.
"""
    name = _build_symlink_name(content)
    assert name is not None
    assert "undated" in name
