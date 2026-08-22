"""Format-agnostic validation for Anomalica record format files.

Checks structural correctness: frontmatter, schema version, YAML syntax,
no HTML tags. Source-type-specific checks (page completeness, required URL)
are handled by callers via the extra_required parameter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fixed: str | None = None


REQUIRED_FRONTMATTER = ["schema", "title", "source_type"]

# A record must evidence WHEN, but either layer satisfies it: `date_published` is the
# work's date, `posted_date` is when the channel posted the copy the fetcher saw.
# date_published was unconditionally required until 2026-08-19, which is why the
# audio handler fabricated one from today's date rather than fail validation - two
# 1972 Apollo debriefings came out dated 2026-07-11. Requiring "one of" lets a fresh
# video ingest record only what it observed and leave the work's date absent, per
# ingest-format's not-evidenced convention.
REQUIRED_ONE_OF = [("date_published", "posted_date")]
CURRENT_SCHEMA = "anomalica/record/1"


def strip_code_fences(content: str) -> str:
    """Strip markdown code fences if the content is wrapped in them."""
    stripped = content.strip()
    if not stripped.startswith("```"):
        return content
    newline_pos = stripped.find("\n")
    if newline_pos >= 0:
        stripped = stripped[newline_pos + 1 :]
    if stripped.rstrip().endswith("```"):
        stripped = stripped.rstrip()[:-3]
    return stripped.strip()


def _fix_yaml_quoting(frontmatter: str) -> str:
    """Fix unquoted YAML values that contain colons."""
    lines = frontmatter.split("\n")
    fixed = []
    for line in lines:
        match = re.match(r"^([a-z_]+): (.+)$", line)
        if match:
            key, value = match.group(1), match.group(2)
            if ":" in value and not value.startswith('"') and not value.startswith("'"):
                value = '"' + value.replace('"', '\\"') + '"'
                line = f"{key}: {value}"
        fixed.append(line)
    return "\n".join(fixed)


_BODY_ANNOTATION = re.compile(r"\{\{.*?\}\}")


def _annotation_leaks(value, path: str = "") -> list[str]:
    """Field paths whose value carries {{...}} body-annotation syntax.

    The {{redacted}}/{{illegible}}/{{classification}} grammar is defined for the
    body only; a consumer reading a frontmatter value takes it as literal text, so
    the syntax leaks (the same class of escape as a classification marker reaching
    the digester). Walks nested mappings and lists to name the exact field."""
    leaks: list[str] = []
    if isinstance(value, str):
        if _BODY_ANNOTATION.search(value):
            leaks.append(path or "(root)")
    elif isinstance(value, dict):
        for key, sub in value.items():
            leaks.extend(_annotation_leaks(sub, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, list):
        for i, sub in enumerate(value):
            leaks.extend(_annotation_leaks(sub, f"{path}[{i}]"))
    return leaks


def validate(
    content: str,
    extra_required: list[str] | None = None,
    expected_schema: str = CURRENT_SCHEMA,
) -> ValidationResult:
    """Validate a record against the Anomalica record format.

    Args:
        content: The full record file content.
        extra_required: Additional frontmatter fields required beyond the
            base set (schema, title, date, source_type).
        expected_schema: The schema version this record should declare
            (defaults to the current v1 schema; word-level records pass
            anomalica/record/2).

    Returns:
        ValidationResult with errors, warnings, and optionally fixed content.
    """
    result = ValidationResult()
    fixed_content = content

    # Check for code fences wrapping the entire output
    stripped = content.strip()
    if stripped.startswith("```"):
        result.errors.append("Content wrapped in code fence - should be stripped")
        fixed_content = strip_code_fences(content)
        result.fixed = fixed_content
        stripped = fixed_content

    # Parse frontmatter
    if not stripped.startswith("---"):
        result.errors.append("No YAML frontmatter found (must start with ---)")
        return result

    parts = stripped.split("---", 2)
    if len(parts) < 3:
        result.errors.append("Incomplete YAML frontmatter (missing closing ---)")
        return result

    # Try to parse frontmatter, auto-fixing unquoted colons if needed
    frontmatter_text = parts[1].strip()
    try:
        frontmatter = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError:
        fixed_fm = _fix_yaml_quoting(parts[1])
        try:
            frontmatter = yaml.safe_load(fixed_fm)
            parts[1] = fixed_fm
            fixed_content = "---".join(parts)
            result.fixed = fixed_content
            result.warnings.append("Auto-fixed: quoted YAML values containing colons")
        except yaml.YAMLError:
            result.errors.append("Frontmatter YAML is invalid - could not parse")
            return result

    if not isinstance(frontmatter, dict):
        result.errors.append("Frontmatter YAML is not a mapping")
        return result

    # A body-annotation ({{...}}) must never appear in a frontmatter value: the
    # grammar is defined for the body, and a frontmatter consumer reads the value as
    # literal text. Reject, never rewrite - {{redacted}} in creators should become
    # [redacted] but {{illegible}} in a title should not become anything, and the
    # validator cannot tell which; name the field and let a human fix it.
    #
    # This check is deliberately LEXICAL and must stay so. Do NOT grow it into a
    # name test (e.g. rejecting an unbracketed creators value that "looks like a
    # description"): name-vs-description is decided at the MINTING layer - the
    # extraction prompt, with the page in front of the model - not re-litigated at
    # validation. A regex would misfire on a pseudonym like "Dr. X" and second-guess
    # a reviewer who deliberately wrote a description. Same rule applied once, at
    # minting, not at every consumer.
    for field_path in _annotation_leaks(frontmatter):
        result.errors.append(
            "Body-annotation syntax ({{...}}) in frontmatter value: "
            + field_path
            + " - {{...}} is body-only; a described person is [bracketed], a "
            "withheld one is [redacted]"
        )

    # Check required fields
    all_required = REQUIRED_FRONTMATTER + (extra_required or [])
    for field_name in all_required:
        if field_name not in frontmatter:
            result.errors.append(f"Missing required frontmatter field: {field_name}")
    for group in REQUIRED_ONE_OF:
        if not any(name in frontmatter for name in group):
            result.errors.append(
                "Missing required frontmatter field: one of " + " / ".join(group)
            )

    # Check schema version
    if frontmatter.get("schema") and frontmatter["schema"] != expected_schema:
        result.errors.append(
            f"Wrong schema version: {frontmatter['schema']} (expected {expected_schema})"
        )

    # Check body content
    body = parts[2].strip()
    if not body:
        result.warnings.append("No content after frontmatter (empty body)")
        return result

    # Check for HTML tags
    html_tags = re.findall(r"<(sup|sub|br|div|span|p|b|i|em|strong)[>\s/]", body)
    if html_tags:
        unique_tags = sorted(set(html_tags))
        result.warnings.append(
            f"HTML tags found (should use markdown instead): "
            f"{', '.join('<' + t + '>' for t in unique_tags)}"
        )

    return result
