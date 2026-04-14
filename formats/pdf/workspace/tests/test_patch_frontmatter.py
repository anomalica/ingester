"""Tests for frontmatter patching, record checking, and page utilities in ingest_pdf."""

from ingest_pdf import (
    _check_record,
    _find_missing_pages,
    _patch_frontmatter,
    _renumber_pages,
    _strip_frontmatter,
)


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


# --- _check_record tests ---


def test_check_record_valid():
    content = "---\nschema: anomalica/record/1\n---\n\n" + "x" * 500
    valid, reason = _check_record(content)
    assert valid
    assert reason == ""


def test_check_record_no_frontmatter():
    valid, reason = _check_record("Just plain text without frontmatter.")
    assert not valid
    assert "frontmatter" in reason


def test_check_record_too_short():
    content = "---\nschema: test\n---\n\nShort."
    valid, reason = _check_record(content, min_chars=1000)
    assert not valid
    assert "too short" in reason


def test_check_record_strips_code_fences():
    content = "```markdown\n---\nschema: test\n---\n\n" + "x" * 500 + "\n```"
    valid, reason = _check_record(content)
    assert valid


def test_check_record_code_fences_no_frontmatter():
    content = "```\nJust some text.\n```"
    valid, reason = _check_record(content)
    assert not valid


# --- _strip_frontmatter tests ---


def test_strip_frontmatter_removes_it():
    content = "---\nschema: test\ntitle: Doc\n---\n\nBody text."
    result = _strip_frontmatter(content)
    assert "Body text." in result
    assert "schema" not in result


def test_strip_frontmatter_no_frontmatter():
    content = "Just body text."
    result = _strip_frontmatter(content)
    assert result == content


def test_strip_frontmatter_incomplete():
    content = "---\nschema: test\nno closing delimiter"
    result = _strip_frontmatter(content)
    assert result == content


# --- _find_missing_pages tests ---


def test_find_missing_pages_none_missing():
    content = "---\n---\n\n<!-- file_page: 1 -->\n\n<!-- file_page: 2 -->\n\n<!-- file_page: 3 -->\n"
    assert _find_missing_pages(content, 3) == []


def test_find_missing_pages_gap():
    content = "---\n---\n\n<!-- file_page: 1 -->\n\n<!-- file_page: 3 -->\n"
    assert _find_missing_pages(content, 3) == [2]


def test_find_missing_pages_truncated():
    content = "---\n---\n\n<!-- file_page: 1 -->\n\n<!-- file_page: 2 -->\n"
    assert _find_missing_pages(content, 5) == [3, 4, 5]


# --- _renumber_pages tests ---


def test_renumber_pages_zero_offset():
    content = "<!-- file_page: 1 -->\nContent."
    assert _renumber_pages(content, 0) == content


def test_renumber_pages_with_offset():
    content = "<!-- file_page: 1 -->\nContent.\n<!-- file_page: 2 -->\nMore."
    result = _renumber_pages(content, 50)
    assert "file_page: 51" in result
    assert "file_page: 52" in result
    assert "file_page: 1" not in result
