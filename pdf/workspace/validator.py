"""Validator for Anomalica record format files.

Checks structural correctness of extracted records. Returns errors (must fix),
warnings (worth investigating), and optionally a fixed version of the content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from extraction import strip_code_fences


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fixed: str | None = None


REQUIRED_FRONTMATTER = ["schema", "title", "date", "source_type"]
CURRENT_SCHEMA = "anomalica/record/1"


def _fix_yaml_quoting(frontmatter: str) -> str:
    """Try to fix unquoted YAML values that contain colons.

    For simple key: value lines where the value contains a colon,
    wrap the value in double quotes.
    """
    lines = frontmatter.split("\n")
    fixed = []
    for line in lines:
        # Match simple key: value lines (not indented list items, not already quoted)
        match = re.match(r"^([a-z_]+): (.+)$", line)
        if match:
            key, value = match.group(1), match.group(2)
            if ":" in value and not value.startswith('"') and not value.startswith("'"):
                value = '"' + value.replace('"', '\\"') + '"'
                line = f"{key}: {value}"
        fixed.append(line)
    return "\n".join(fixed)


def _parse_annotation_blocks(body: str) -> list[dict]:
    """Parse YAML annotation blocks from the body content.

    Splits on --- delimiters and attempts YAML parse on each block between
    consecutive delimiters. Returns a list of successfully parsed dicts.
    """
    lines = body.split("\n")
    delimiter_indices = [i for i, line in enumerate(lines) if line.strip() == "---"]

    annotations = []
    i = 0
    while i < len(delimiter_indices) - 1:
        start = delimiter_indices[i]
        end = delimiter_indices[i + 1]
        block_lines = lines[start + 1 : end]
        block_text = "\n".join(block_lines).strip()

        if not block_text:
            i += 2
            continue

        try:
            block = yaml.safe_load(block_text)
            if isinstance(block, dict):
                annotations.append(block)
                i += 2
                continue
        except yaml.YAMLError:
            pass

        # Not valid YAML - skip this delimiter and try pairing the next one
        i += 1

    return annotations


def validate(content: str) -> ValidationResult:
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

    # Check required fields
    for field_name in REQUIRED_FRONTMATTER:
        if field_name not in frontmatter:
            result.errors.append(f"Missing required frontmatter field: {field_name}")

    # Check schema version
    if frontmatter.get("schema") and frontmatter["schema"] != CURRENT_SCHEMA:
        result.errors.append(
            f"Wrong schema version: {frontmatter['schema']} (expected {CURRENT_SCHEMA})"
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
            f"HTML tags found (should use markdown instead): {', '.join('<' + t + '>' for t in unique_tags)}"
        )

    # Parse annotation blocks and check structure
    annotations = _parse_annotation_blocks(body)

    # Extract page info
    file_pages = []
    has_old_page_field = False

    for block in annotations:
        if "file_page" in block:
            file_pages.append(block["file_page"])
        if "page" in block and "file_page" not in block:
            has_old_page_field = True

    if has_old_page_field:
        result.warnings.append(
            "Found 'page:' annotation without 'file_page:' - should use file_page instead"
        )

    # Check page sequence and completeness
    expected_pages = frontmatter.get("pages")
    if file_pages:
        missing_pages = []
        for i in range(1, len(file_pages)):
            expected = file_pages[i - 1] + 1
            actual = file_pages[i]
            if actual != expected:
                missing_pages.extend(range(expected, actual))

        if missing_pages:
            # A few missing pages is a warning; many is an error
            if len(missing_pages) > 3:
                result.errors.append(
                    f"Significant content missing: {len(missing_pages)} pages "
                    f"not found in output ({missing_pages[0]}-{missing_pages[-1]})"
                )
            else:
                for p in missing_pages:
                    result.warnings.append(f"Missing page annotation for file_page {p}")

    if expected_pages and file_pages:
        max_page = max(file_pages)
        if max_page != expected_pages:
            missing_count = expected_pages - max_page
            if missing_count > expected_pages * 0.25:
                result.errors.append(
                    f"Output truncated: only {max_page} of {expected_pages} pages extracted"
                )
            else:
                result.warnings.append(
                    f"Frontmatter says pages: {expected_pages} but highest "
                    f"file_page annotation is {max_page}"
                )

    return result
