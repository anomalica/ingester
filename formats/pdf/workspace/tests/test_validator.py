"""Tests for record format validator (shared)."""

from shared.validator import validate


VALID_RECORD = """---
schema: anomalica/record/1
title: Test Document
date_published: 2023-07-26
source_type: pdf
pages: 2
---

---
file_page: 1
---

Some content.

---
file_page: 2
---

More content.
"""


def test_valid_record_passes():
    result = validate(VALID_RECORD)
    assert not result.errors


def test_missing_schema():
    record = """---
title: Test
date_published: 2023-07-26
source_type: pdf
---

Content.
"""
    result = validate(record)
    assert any("schema" in e for e in result.errors)


def test_wrong_schema_version():
    record = """---
schema: anomalica/record/99
title: Test
date_published: 2023-07-26
source_type: pdf
---

Content.
"""
    result = validate(record)
    assert any("schema" in e for e in result.errors)


def test_missing_title():
    record = """---
schema: anomalica/record/1
date_published: 2023-07-26
source_type: pdf
---

Content.
"""
    result = validate(record)
    assert any("title" in e for e in result.errors)


def test_missing_date():
    record = """---
schema: anomalica/record/1
title: Test
source_type: pdf
---

Content.
"""
    result = validate(record)
    assert any("date" in e for e in result.errors)


def test_missing_source_type():
    record = """---
schema: anomalica/record/1
title: Test
date_published: 2023-07-26
---

Content.
"""
    result = validate(record)
    assert any("source_type" in e for e in result.errors)


def test_code_fences_detected():
    record = """```markdown
---
schema: anomalica/record/1
title: Test
date_published: 2023-07-26
source_type: pdf
---

Content.
```"""
    result = validate(record)
    assert any("code fence" in e for e in result.errors)


def test_trailing_delimiter_is_harmless():
    """A trailing --- with no matching pair should not cause errors."""
    record = """---
schema: anomalica/record/1
title: Test
date_published: 2023-07-26
source_type: pdf
---

---
file_page: 1
---

Content.

---
"""
    result = validate(record)
    assert not result.errors


def test_invalid_frontmatter_yaml():
    record = """---
schema: anomalica/record/1
title: Test
date_published: 2023-07-26
source_type: pdf
  bad_indent: true
---

Content.
"""
    result = validate(record)
    assert any(
        "frontmatter" in e.lower() and "yaml" in e.lower() for e in result.errors
    )


def test_autofix_strips_code_fences():
    record = """```markdown
---
schema: anomalica/record/1
title: Test
date_published: 2023-07-26
source_type: pdf
---

Content.
```"""
    result = validate(record)
    assert result.fixed is not None
    assert not result.fixed.startswith("```")
    assert not result.fixed.endswith("```")


def test_empty_content_warning():
    record = """---
schema: anomalica/record/1
title: Test
date_published: 2023-07-26
source_type: pdf
---
"""
    result = validate(record)
    assert any(
        "empty" in w.lower() or "no content" in w.lower() for w in result.warnings
    )


def test_autofix_yaml_quoting():
    """Titles with colons should be auto-quoted."""
    record = """---
schema: anomalica/record/1
title: Report: Volume 1
date_published: 2023-07-26
source_type: pdf
---

Content.
"""
    result = validate(record)
    assert result.fixed is not None
    assert any("quoted" in w.lower() for w in result.warnings)
    # The fixed version should parse correctly
    import yaml

    parts = result.fixed.split("---", 2)
    fm = yaml.safe_load(parts[1])
    assert fm["title"] == "Report: Volume 1"
