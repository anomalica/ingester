"""Validator for Anomalica record format files.

Checks structural correctness of extracted records. Returns errors (must fix),
warnings (worth investigating), and optionally a fixed version of the content.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fixed: str | None = None


REQUIRED_FRONTMATTER = ["schema", "title", "date", "source_type"]
CURRENT_SCHEMA = "anomalica/record/1"


def validate(content: str) -> ValidationResult:
    result = ValidationResult()
    fixed_content = content

    # Check for code fences wrapping the entire output
    stripped = content.strip()
    if stripped.startswith("```"):
        result.errors.append("Content wrapped in code fence - should be stripped")
        newline_pos = stripped.find("\n")
        if newline_pos >= 0:
            fixed_content = stripped[newline_pos + 1 :]
        if fixed_content.rstrip().endswith("```"):
            fixed_content = fixed_content.rstrip()[:-3].rstrip()
        result.fixed = fixed_content
        stripped = fixed_content.strip()

    # Parse frontmatter
    if not stripped.startswith("---"):
        result.errors.append("No YAML frontmatter found (must start with ---)")
        return result

    parts = stripped.split("---", 2)
    if len(parts) < 3:
        result.errors.append("Incomplete YAML frontmatter (missing closing ---)")
        return result

    frontmatter_text = parts[1].strip()
    try:
        frontmatter = yaml.safe_load(frontmatter_text)
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

    # Parse annotation blocks and check structure
    _check_annotations(body, frontmatter, result)

    return result


def _check_annotations(body: str, frontmatter: dict, result: ValidationResult) -> None:
    """Check annotation blocks in the body for structural issues."""
    # Split body on --- lines to find annotation blocks
    lines = body.split("\n")
    delimiter_indices = [i for i, line in enumerate(lines) if line.strip() == "---"]

    # Delimiters should come in pairs
    if len(delimiter_indices) % 2 != 0:
        result.warnings.append(
            f"Odd number of --- delimiters in body ({len(delimiter_indices)}) - "
            f"possibly unbalanced annotation block"
        )

    # Extract file_page values
    file_pages = []
    has_old_page_field = False

    for i in range(0, len(delimiter_indices) - 1, 2):
        start = delimiter_indices[i]
        end = delimiter_indices[i + 1]
        block_lines = lines[start + 1 : end]
        block_text = "\n".join(block_lines).strip()

        try:
            block = yaml.safe_load(block_text)
        except yaml.YAMLError:
            continue

        if not isinstance(block, dict):
            continue

        if "file_page" in block:
            file_pages.append(block["file_page"])
        if "page" in block and "file_page" not in block:
            has_old_page_field = True

    if has_old_page_field:
        result.warnings.append(
            "Found 'page:' annotation without 'file_page:' - should use file_page instead"
        )

    # Check page sequence
    if file_pages:
        for i in range(1, len(file_pages)):
            expected = file_pages[i - 1] + 1
            actual = file_pages[i]
            if actual != expected:
                for missing in range(expected, actual):
                    result.warnings.append(
                        f"Missing page annotation for file_page {missing}"
                    )

    # Check page count matches frontmatter
    expected_pages = frontmatter.get("pages")
    if expected_pages and file_pages:
        max_page = max(file_pages)
        if max_page != expected_pages:
            result.warnings.append(
                f"Frontmatter says pages: {expected_pages} but highest "
                f"file_page annotation is {max_page}"
            )
