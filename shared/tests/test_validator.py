from validator import validate


VALID_RECORD = """---
schema: anomalica/record/1
title: Test Document
date_published: 2023-07-26
source_type: web
source_url: https://example.com
---

Article content here.
"""

VALID_RECORD_CODE_FENCED = """```markdown
---
schema: anomalica/record/1
title: Test Document
date_published: 2023-07-26
source_type: web
---

Article content here.
```"""

RECORD_WITH_COLON_IN_TITLE = """---
schema: anomalica/record/1
title: Document: A Subtitle
date_published: 2023-07-26
source_type: web
---

Content here.
"""


def test_valid_record_no_errors():
    result = validate(VALID_RECORD)
    assert result.errors == []


def test_body_annotation_in_frontmatter_value_is_rejected():
    """{{...}} is body-only. A model that put the {{redacted}} marker in a creators
    field leaked body-annotation syntax into frontmatter, where a consumer reads it
    as literal text - the same class of escape as a classification marker reaching
    the digester. It is rejected (not rewritten), naming the field."""
    record = """---
schema: anomalica/record/1
title: Test Document
date_published: 2023-07-26
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
date_published: 2023-07-26
source_type: pdf
---

Body.
"""
    assert any("title" in e and "Body-annotation" in e for e in validate(leaky).errors)

    clean = """---
schema: anomalica/record/1
title: Test Document
date_published: 2023-07-26
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
date_published: 2023-07-26
---

Content.
"""
    result = validate(record)
    assert any("source_type" in e for e in result.errors)


def test_wrong_schema_version():
    record = """---
schema: anomalica/record/99
title: Test
date_published: 2023-07-26
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
date_published: 2023-07-26
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
date_published: 2023-07-26
source_type: web
---
"""
    result = validate(record)
    assert any("empty" in w.lower() for w in result.warnings)


def test_extra_required_field_missing():
    record = """---
schema: anomalica/record/1
title: Test
date_published: 2023-07-26
source_type: web
---

Content.
"""
    result = validate(record, extra_required=["source_url"])
    assert any("source_url" in e for e in result.errors)


def test_extra_required_field_present():
    result = validate(VALID_RECORD, extra_required=["source_url"])
    assert result.errors == []
