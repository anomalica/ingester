import pytest

from document_type import DOCUMENT_TYPES
from validator import validate


VALID_RECORD = """---
schema: anomalica/record/1
title: Test Document
date_published: "2023-07-26"
source_type: web
source_url: https://example.com
---

Article content here.
"""

VALID_RECORD_CODE_FENCED = """```markdown
---
schema: anomalica/record/1
title: Test Document
date_published: "2023-07-26"
source_type: web
---

Article content here.
```"""

RECORD_WITH_COLON_IN_TITLE = """---
schema: anomalica/record/1
title: Document: A Subtitle
date_published: "2023-07-26"
source_type: web
---

Content here.
"""


def test_valid_record_no_errors():
    result = validate(VALID_RECORD)
    assert result.errors == []


@pytest.mark.parametrize("document_type", DOCUMENT_TYPES)
def test_every_canonical_document_type_is_valid(document_type):
    record = VALID_RECORD.replace(
        "source_type: web\n", f"source_type: web\ndocument_type: {document_type}\n"
    )

    assert validate(record).errors == []


@pytest.mark.parametrize(
    "declaration",
    [
        "document_type: null",
        "document_type: []",
        "document_type:\n  - footage",
        "document_type: 1",
        "document_type: true",
        'document_type: ""',
        'document_type: "   "',
        "document_type: Footage",
        "document_type: screenplay",
    ],
)
def test_invalid_present_document_type_is_rejected_without_coercion(declaration):
    record = VALID_RECORD.replace(
        "source_type: web", f"source_type: web\n{declaration}"
    )

    result = validate(record)

    assert any("Invalid document_type" in error for error in result.errors)


def test_body_annotation_in_frontmatter_value_is_rejected():
    """{{...}} is body-only. A model that put the {{redacted}} marker in a creators
    field leaked body-annotation syntax into frontmatter, where a consumer reads it
    as literal text - the same class of escape as a classification marker reaching
    the digester. It is rejected (not rewritten), naming the field."""
    record = """---
schema: anomalica/record/1
title: Test Document
date_published: "2023-07-26"
source_type: pdf
creators:
  - "{{redacted}}"
---

Body.
"""
    result = validate(record)
    assert any("Body-annotation syntax" in e and "creators" in e for e in result.errors)


def test_body_annotation_detected_at_any_nesting_and_bracketed_forms_pass():
    """The scan walks nested mappings, and the sanctioned replacements - a
    [bracketed] description and [redacted] - are not {{...}} and must pass clean."""
    leaky = """---
schema: anomalica/record/1
title: "A {{illegible}} title"
date_published: "2023-07-26"
source_type: pdf
---

Body.
"""
    assert any("title" in e and "Body-annotation" in e for e in validate(leaky).errors)

    clean = """---
schema: anomalica/record/1
title: Test Document
date_published: "2023-07-26"
source_type: pdf
creators:
  - "[senior US intelligence officer]"
  - "[redacted]"
---

Body.
"""
    assert not any("Body-annotation" in e for e in validate(clean).errors)


def test_missing_frontmatter():
    result = validate("No frontmatter here")
    assert any("No YAML frontmatter" in e for e in result.errors)


def test_incomplete_frontmatter():
    result = validate("---\ntitle: Test\n")
    assert any("Incomplete" in e or "missing" in e.lower() for e in result.errors)


def test_missing_required_field():
    record = """---
schema: anomalica/record/1
title: Test
date_published: "2023-07-26"
---

Content.
"""
    result = validate(record)
    assert any("source_type" in e for e in result.errors)


def test_wrong_schema_version():
    record = """---
schema: anomalica/record/99
title: Test
date_published: "2023-07-26"
source_type: web
---

Content.
"""
    result = validate(record)
    assert any(
        "schema version" in e.lower() or "Wrong schema" in e for e in result.errors
    )


def test_code_fence_stripped():
    result = validate(VALID_RECORD_CODE_FENCED)
    assert result.fixed is not None
    assert not result.fixed.strip().startswith("```")


def test_yaml_colon_auto_fix():
    result = validate(RECORD_WITH_COLON_IN_TITLE)
    # Should either parse OK or auto-fix
    assert not any("invalid" in e.lower() for e in result.errors)


def test_html_tags_warned():
    record = """---
schema: anomalica/record/1
title: Test
date_published: "2023-07-26"
source_type: web
---

Text with <sup>1</sup> superscript.
"""
    result = validate(record)
    assert any("HTML" in w for w in result.warnings)


def test_empty_body_warned():
    record = """---
schema: anomalica/record/1
title: Test
date_published: "2023-07-26"
source_type: web
---
"""
    result = validate(record)
    assert any("empty" in w.lower() for w in result.warnings)


def test_extra_required_field_missing():
    record = """---
schema: anomalica/record/1
title: Test
date_published: "2023-07-26"
source_type: web
---

Content.
"""
    result = validate(record, extra_required=["source_url"])
    assert any("source_url" in e for e in result.errors)


def test_extra_required_field_present():
    result = validate(VALID_RECORD, extra_required=["source_url"])
    assert result.errors == []


@pytest.mark.parametrize(
    "value",
    [
        "1988",
        "2020-08",
        "2020-08-09",
        "2020-08-09T17:30:00Z",
        "2020-08-09T17:30:00+09:00",
        "2020-08-09T17:30:00-04:30",
    ],
)
@pytest.mark.parametrize("field", ["date_published", "posted_date"])
def test_publication_fields_accept_evidenced_precision_and_offsets(field, value):
    record = VALID_RECORD.replace('date_published: "2023-07-26"', f'{field}: "{value}"')
    assert validate(record).errors == []


@pytest.mark.parametrize(
    "declaration",
    [
        "date_published: 2020-08-09",
        'date_published: "2020-02-30"',
        'date_published: "2020-08-09T17:30:00"',
        'posted_date: "2020-13"',
    ],
)
def test_publication_fields_reject_non_strings_invalid_dates_and_naive_times(
    declaration,
):
    record = VALID_RECORD.replace('date_published: "2023-07-26"', declaration)
    assert any("Invalid" in error for error in validate(record).errors)


@pytest.mark.parametrize("value", ["2020-08-09T17:30:00Z", "2020-08-09T17:30:00+09:00"])
def test_date_accessed_accepts_offset_instants(value):
    record = VALID_RECORD.replace(
        "source_type: web", f'source_type: web\ndate_accessed: "{value}"'
    )
    assert validate(record).errors == []


@pytest.mark.parametrize("value", ["2020-08-09", "2020-08-09T17:30:00"])
def test_date_accessed_rejects_dates_and_naive_times(value):
    record = VALID_RECORD.replace(
        "source_type: web", f'source_type: web\ndate_accessed: "{value}"'
    )
    assert any("date_accessed" in error for error in validate(record).errors)


def test_date_extracted_requires_quoted_utc_z():
    valid = VALID_RECORD.replace(
        "source_type: web", 'source_type: web\ndate_extracted: "2020-08-09T08:30:00Z"'
    )
    offset = valid.replace("08:30:00Z", "17:30:00+09:00")
    bare = valid.replace('"2020-08-09T08:30:00Z"', "2020-08-09T08:30:00Z")
    assert validate(valid).errors == []
    assert any("date_extracted" in error for error in validate(offset).errors)
    assert any("date_extracted" in error for error in validate(bare).errors)


def test_release_date_is_a_quoted_full_date_only():
    valid = VALID_RECORD.replace(
        "source_type: web",
        'source_type: web\nrelease:\n  release_date: "2020-08-09"',
    )
    partial = valid.replace("2020-08-09", "2020-08")
    assert validate(valid).errors == []
    assert any("release.release_date" in error for error in validate(partial).errors)


def test_publication_date_is_optional_when_not_evidenced():
    record = VALID_RECORD.replace('date_published: "2023-07-26"\n', "")
    assert validate(record).errors == []


def test_message_annotation_date_requires_a_quoted_offset_timestamp():
    valid = VALID_RECORD.replace(
        "Article content here.",
        '<!-- message: {n: 1, date: "2020-08-09T17:30:00+09:00", quoted: false} -->\nBody.',
    )
    naive = valid.replace("17:30:00+09:00", "17:30:00")
    bare = valid.replace('"2020-08-09T17:30:00+09:00"', "2020-08-09T17:30:00+09:00")
    assert validate(valid).errors == []
    assert any("annotations.message.date" in error for error in validate(naive).errors)
    assert any("annotations.message.date" in error for error in validate(bare).errors)


@pytest.mark.parametrize("path", ["review_carryover", "refresh_refused"])
def test_machine_event_blocks_require_quoted_utc_z(path):
    valid = VALID_RECORD.replace(
        "source_type: web",
        f'source_type: web\n{path}:\n  at: "2020-08-09T08:30:00Z"',
    )
    offset = valid.replace("08:30:00Z", "17:30:00+09:00")
    assert validate(valid).errors == []
    assert any(f"{path}.at" in error for error in validate(offset).errors)
