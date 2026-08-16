"""Tests for frontmatter patching, record checking, and page utilities in ingest_pdf."""

import re

from ingest_pdf import (
    _check_record,
    _find_missing_pages,
    _normalise_thematic_breaks,
    _preserve_identity,
    _patch_frontmatter,
    _renumber_pages,
    _resequence_pages_sequential,
    _strip_frontmatter,
)


def test_normalise_thematic_breaks_converts_body_rule():
    """A bare '---' in the body collides with the YAML delimiter and truncates a
    naive parser; it must become '***', while the frontmatter delimiters stay."""
    content = "---\nschema: anomalica/record/1\nsource_type: pdf\n---\nfirst\n\n---\n\nsecond\n"
    out = _normalise_thematic_breaks(content)
    # Frontmatter delimiters untouched.
    assert out.startswith("---\nschema: anomalica/record/1\nsource_type: pdf\n---\n")
    body = out.split("\n---\n", 1)[1]
    # No bare '---' survives in the body; the break became '***'.
    assert not any(line.strip() == "---" for line in body.splitlines())
    assert "***" in body


def test_normalise_thematic_breaks_noop_without_body_rule():
    content = "---\nsource_type: pdf\n---\njust text, no rule\n"
    assert _normalise_thematic_breaks(content) == content


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


def test_injects_source_url_and_id():
    content = (
        "---\nschema: anomalica/record/1\nsource_type: pdf\npages: 3\n---\n\nBody."
    )
    result = _patch_frontmatter(
        content,
        "abc123",
        3,
        source_url="https://example.com/doc.pdf",
        source_id="url:example.com/doc.pdf",
    )
    assert "source_url: https://example.com/doc.pdf" in result
    assert "source_id: url:example.com/doc.pdf" in result


def test_injects_source_file_for_local_pdf():
    content = (
        "---\nschema: anomalica/record/1\nsource_type: pdf\npages: 3\n---\n\nBody."
    )
    result = _patch_frontmatter(content, "abc123", 3, source_file="report.pdf")
    assert "source_file: report.pdf" in result


def test_does_not_duplicate_source_url():
    content = "---\nschema: anomalica/record/1\nsource_url: https://a.test/x.pdf\npages: 3\n---\n\nBody."
    result = _patch_frontmatter(content, "abc123", 3, source_url="https://a.test/x.pdf")
    assert result.count("source_url:") == 1


def test_omits_absent_source_fields():
    content = (
        "---\nschema: anomalica/record/1\nsource_type: pdf\npages: 3\n---\n\nBody."
    )
    result = _patch_frontmatter(content, "abc123", 3)
    assert "source_url:" not in result
    assert "source_id:" not in result
    assert "source_file:" not in result
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


def test_preserve_identity_holds_stored_title_and_date():
    """On a re-ingest the model's re-derived title/date are overridden by the stored
    values, so a re-extraction never renames or re-dates a record."""
    content = '---\ntitle: "Re-derived"\ndate_published: 2020-08-09\nsource_type: pdf\n---\nbody'
    out = _preserve_identity(
        content, {"title": "Stored Title", "date_published": "2020-05-14"}
    )
    assert 'title: "Stored Title"' in out
    assert "date_published: 2020-05-14" in out
    assert out.endswith("body")


def test_preserve_identity_renames_model_date_field():
    """A preserved year-only date is written QUOTED: bare `1975` is a YAML integer,
    so the field would parse as a number beside neighbours that parse as dates."""
    content = '---\ntitle: "T"\ndate: 2020-08-09\n---\nbody'
    out = _preserve_identity(content, {"date_published": "1975"})
    assert 'date_published: "1975"' in out
    assert "\ndate: " not in out


def test_preserve_identity_noop_when_nothing_preserved():
    content = '---\ntitle: "T"\n---\nbody'
    assert _preserve_identity(content, {}) == content


# --- _resequence_pages_sequential tests ---


def test_resequence_fixes_chunk_offset_double_count():
    """The dominant defect: chunk 2's pages emitted as absolute (21-25) then the
    merge added the offset again (41-45). Complete marker set, wrong values ->
    resequenced to 1..5 by order."""
    content = (
        "---\n---\n"
        "<!-- file_page: 1 -->\na\n<!-- file_page: 2 -->\nb\n"
        "<!-- file_page: 41 -->\nc\n<!-- file_page: 42 -->\nd\n<!-- file_page: 43 -->\ne\n"
    )
    fixed, changed = _resequence_pages_sequential(content, 5)
    assert changed
    assert re.findall(r"file_page: (\d+)", fixed) == ["1", "2", "3", "4", "5"]


def test_resequence_fixes_printed_number_substitution():
    """A page carrying a prominent printed number (31) that the model copied into
    file_page on a 3-page doc is corrected by position."""
    content = "<!-- file_page: 1 -->\na\n<!-- file_page: 2 -->\nb\n<!-- file_page: 31 -->\nc\n"
    fixed, changed = _resequence_pages_sequential(content, 3)
    assert changed
    assert re.findall(r"file_page: (\d+)", fixed) == ["1", "2", "3"]


def test_resequence_noop_when_already_sequential():
    content = "<!-- file_page: 1 -->\na\n<!-- file_page: 2 -->\nb\n"
    fixed, changed = _resequence_pages_sequential(content, 2)
    assert not changed
    assert fixed == content


def test_resequence_noop_when_count_mismatch():
    """A short marker count is a genuinely missing/merged page - left for repair,
    never masked by renumbering."""
    content = "<!-- file_page: 1 -->\na\n<!-- file_page: 31 -->\nb\n"
    fixed, changed = _resequence_pages_sequential(content, 3)
    assert not changed
    assert fixed == content


def test_model_datetime_is_normalised_to_a_bare_date():
    """The extraction model authors this field, so the prompt cannot guarantee its
    shape - it has emitted datetimes where every other handler writes a bare date,
    and YAML then parses one field as two types across the corpus."""
    content = (
        "---\nschema: anomalica/record/1\nsource_type: pdf\n"
        "date_published: 2023-07-26 00:00:00+00:00\npages: 1\n---\n\nBody text."
    )
    result = _patch_frontmatter(content, "abc123", 1)
    assert "date_published: 2023-07-26\n" in result


def test_a_year_only_date_is_left_alone():
    """A document that evidences only a year gets a year; padding it to 2023-01-01
    would state a day the document does not."""
    content = (
        "---\nschema: anomalica/record/1\nsource_type: pdf\n"
        "date_published: 1972\npages: 1\n---\n\nBody text."
    )
    result = _patch_frontmatter(content, "abc123", 1)
    assert 'date_published: "1972"\n' in result


def test_authors_is_renamed_to_creators():
    """`creators` is the medium-neutral field. The prompt asks for it, but a
    model-authored frontmatter can still arrive with `authors` - 10 records did
    before the handlers were reconciled."""
    content = (
        "---\nschema: anomalica/record/1\nsource_type: pdf\n"
        "authors:\n  - Eric W. Davis\npages: 1\n---\n\nBody text."
    )
    result = _patch_frontmatter(content, "abc123", 1)
    assert "creators:\n  - Eric W. Davis" in result
    assert "authors:" not in result


def test_authors_is_left_alone_when_creators_already_exists():
    """Two lists is a review problem; silently merging them would be a guess."""
    content = (
        "---\nschema: anomalica/record/1\nsource_type: pdf\n"
        "creators:\n  - Real Author\nauthors:\n  - Someone Else\npages: 1\n---\n\nBody."
    )
    result = _patch_frontmatter(content, "abc123", 1)
    assert "creators:\n  - Real Author" in result
    assert "authors:\n  - Someone Else" in result
