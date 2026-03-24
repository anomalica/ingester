"""Tests for frontmatter patching in ingest_pdf."""

import sys

sys.path.insert(0, "workspace")
from ingest_pdf import _patch_frontmatter


def test_injects_content_hash():
    content = (
        "---\nschema: anomalica/record/1\nsource_type: pdf\npages: 3\n---\n\nBody text."
    )
    result = _patch_frontmatter(content, "abc123", 3)
    assert "content_hash: sha256:abc123" in result
    assert "Body text." in result


def test_fixes_page_count():
    content = "---\nschema: anomalica/record/1\npages: 20\n---\n\nBody text."
    result = _patch_frontmatter(content, "abc123", 54)
    assert "pages: 54" in result
    assert "pages: 20" not in result


def test_does_not_modify_body():
    content = "---\nschema: anomalica/record/1\npages: 20\n---\n\nThe report has pages: 42 in total."
    result = _patch_frontmatter(content, "abc123", 54)
    # Frontmatter should be fixed
    assert result.startswith("---\nschema: anomalica/record/1\npages: 54")
    # Body should be untouched
    assert "pages: 42 in total" in result


def test_does_not_duplicate_content_hash():
    content = "---\nschema: anomalica/record/1\ncontent_hash: sha256:existing\npages: 3\n---\n\nBody."
    result = _patch_frontmatter(content, "newhash", 3)
    assert "content_hash: sha256:existing" in result
    assert "newhash" not in result


def test_handles_no_frontmatter():
    content = "Just some text without frontmatter."
    result = _patch_frontmatter(content, "abc123", 5)
    assert result == content
