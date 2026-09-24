"""Format-agnostic validation for Anomalica record format files.

Checks structural correctness: frontmatter, schema version, YAML syntax,
no HTML tags. Source-type-specific checks (page completeness, required URL)
are handled by callers via the extra_required parameter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml
from anomalica_common.repository_privacy import newly_unsafe

try:
    from dates import (
        is_evidenced_date,
        is_full_date,
        is_rfc3339_instant,
        is_utc_instant,
        normalise_published,
    )
except ModuleNotFoundError:
    from shared.dates import (
        is_evidenced_date,
        is_full_date,
        is_rfc3339_instant,
        is_utc_instant,
        normalise_published,
    )

try:
    from document_type import DOCUMENT_TYPES
except ModuleNotFoundError:
    from shared.document_type import DOCUMENT_TYPES


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fixed: str | None = None


REQUIRED_FRONTMATTER = ["schema", "title"]

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
    allow_legacy_temporal: bool = False,
) -> ValidationResult:
    """Validate a record against the Anomalica record format.

    Args:
        content: The full record file content.
        extra_required: Additional frontmatter fields required beyond the
            base set (schema, title, source_type).
        expected_schema: The schema version this record should declare
            (defaults to the current v1 schema; word-level records pass
            anomalica/record/2).
        allow_legacy_temporal: Accept documented legacy lexical forms only when
            preserving an existing record through a non-temporal edit.

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

    for field_path in newly_unsafe(frontmatter):
        result.errors.append(f"Local machine location in frontmatter: {field_path}")

    def valid_published(value: object) -> bool:
        if isinstance(value, str) and is_evidenced_date(value):
            return True
        if not allow_legacy_temporal:
            return False
        if isinstance(value, int) and len(str(value)) == 4:
            return is_evidenced_date(str(value))
        return is_evidenced_date(value) or is_evidenced_date(normalise_published(value))

    def valid_offset(value: object, *, date_legacy: bool = False) -> bool:
        if isinstance(value, str) and is_rfc3339_instant(value):
            return True
        if not allow_legacy_temporal:
            return False
        if date_legacy and is_full_date(value):
            return True
        return is_rfc3339_instant(value) or is_rfc3339_instant(
            normalise_published(value)
        )

    def valid_utc(value: object) -> bool:
        if is_utc_instant(value):
            return True
        return allow_legacy_temporal and (
            is_rfc3339_instant(value) or is_rfc3339_instant(normalise_published(value))
        )

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
    if frontmatter.get("schema") != "anomalica/record/3":
        all_required = all_required + ["source_type"]
    for field_name in all_required:
        if field_name not in frontmatter:
            result.errors.append(f"Missing required frontmatter field: {field_name}")

    for field_name in ("date_published", "posted_date"):
        if field_name in frontmatter and not valid_published(frontmatter[field_name]):
            result.errors.append(
                f"Invalid {field_name}: expected YYYY, YYYY-MM, YYYY-MM-DD, or "
                "an RFC 3339 timestamp with Z or an explicit offset"
            )

    if "date_accessed" in frontmatter and not valid_offset(
        frontmatter["date_accessed"], date_legacy=True
    ):
        result.errors.append(
            "Invalid date_accessed: expected an RFC 3339 timestamp with Z or "
            "an explicit offset"
        )

    if "date_extracted" in frontmatter:
        extracted = frontmatter["date_extracted"]
        if not valid_utc(extracted):
            result.errors.append(
                "Invalid date_extracted: expected an RFC 3339 UTC timestamp ending in Z"
            )

    provenance = frontmatter.get("provenance")
    if isinstance(provenance, dict):
        if "published_date" in provenance and not valid_published(
            provenance["published_date"]
        ):
            result.errors.append(
                "Invalid provenance.published_date: expected YYYY, YYYY-MM, "
                "YYYY-MM-DD, or an RFC 3339 timestamp with Z or an explicit offset"
            )
        if "posted_date" in provenance and not valid_published(
            provenance["posted_date"]
        ):
            result.errors.append(
                "Invalid provenance.posted_date: expected YYYY, YYYY-MM, "
                "YYYY-MM-DD, or an RFC 3339 timestamp with Z or an explicit offset"
            )
        if "acquired_date" in provenance and not valid_offset(
            provenance["acquired_date"], date_legacy=True
        ):
            result.errors.append(
                "Invalid provenance.acquired_date: expected an RFC 3339 timestamp "
                "with Z or an explicit offset"
            )

    release = frontmatter.get("release")
    if isinstance(release, dict) and "release_date" in release:
        release_date = release["release_date"]
        if not (
            (isinstance(release_date, str) and is_full_date(release_date))
            or (
                allow_legacy_temporal
                and (is_full_date(release_date) or isinstance(release_date, str))
            )
        ):
            result.errors.append("Invalid release.release_date: expected YYYY-MM-DD")

    copyright_block = frontmatter.get("copyright")
    if isinstance(copyright_block, dict):
        for field_name in ("granted_at", "expires"):
            if field_name in copyright_block and not (
                (
                    isinstance(copyright_block[field_name], str)
                    and is_full_date(copyright_block[field_name])
                )
                or (allow_legacy_temporal and is_full_date(copyright_block[field_name]))
            ):
                result.errors.append(
                    f"Invalid copyright.{field_name}: expected YYYY-MM-DD"
                )

    assets = frontmatter.get("assets")
    if isinstance(assets, list):
        for index, asset in enumerate(assets):
            if not isinstance(asset, dict):
                continue
            acquisition = asset.get("acquisition")
            if isinstance(acquisition, dict) and not valid_offset(
                acquisition.get("acquired_at")
            ):
                result.errors.append(
                    f"Invalid assets[{index}].acquisition.acquired_at: expected an "
                    "RFC 3339 timestamp with Z or an explicit offset"
                )
            rights = asset.get("copyright")
            if isinstance(rights, dict):
                for field_name in ("granted_at", "expires"):
                    if field_name in rights and not (
                        isinstance(rights[field_name], str)
                        and is_full_date(rights[field_name])
                    ):
                        result.errors.append(
                            f"Invalid assets[{index}].copyright.{field_name}: "
                            "expected YYYY-MM-DD"
                        )

    for block_name in ("review_carryover", "refresh_refused"):
        block = frontmatter.get(block_name)
        if isinstance(block, dict) and "at" in block and not valid_utc(block["at"]):
            result.errors.append(
                f"Invalid {block_name}.at: expected an RFC 3339 UTC timestamp ending in Z"
            )

    snapshots = frontmatter.get("snapshots")
    if isinstance(snapshots, list):
        for index, snapshot in enumerate(snapshots):
            if isinstance(snapshot, dict) and "captured_at" in snapshot:
                if not valid_utc(snapshot["captured_at"]):
                    result.errors.append(
                        f"Invalid snapshots[{index}].captured_at: expected an RFC "
                        "3339 UTC timestamp ending in Z"
                    )

    processing = frontmatter.get("processing")
    if isinstance(processing, dict):
        source = processing.get("source")
        if isinstance(source, dict) and isinstance(source.get("audio"), list):
            for index, audio in enumerate(source["audio"]):
                if isinstance(audio, dict) and "fetched_at" in audio:
                    if not valid_offset(audio["fetched_at"]):
                        result.errors.append(
                            "Invalid processing.source.audio"
                            f"[{index}].fetched_at: expected an RFC 3339 timestamp "
                            "with Z or an explicit offset"
                        )

    # Check schema version
    if frontmatter.get("schema") and frontmatter["schema"] != expected_schema:
        result.errors.append(
            f"Wrong schema version: {frontmatter['schema']} (expected {expected_schema})"
        )

    if frontmatter.get("schema") == "anomalica/record/3":
        try:
            from anomalica_common.records import Record3Structure

            try:
                from record3 import validate_record3_snapshots
            except ModuleNotFoundError:
                from shared.record3 import validate_record3_snapshots

            Record3Structure.from_frontmatter(frontmatter)
            validate_record3_snapshots(frontmatter)
        except (ImportError, ValueError) as exc:
            result.errors.append(f"Invalid record/3 structure: {exc}")
        source_types = frontmatter.get("source_types")
        derived_source_types = []
        for asset in assets if isinstance(assets, list) else []:
            source_type = asset.get("source_type") if isinstance(asset, dict) else None
            if source_type and source_type not in derived_source_types:
                derived_source_types.append(source_type)
        if not source_types or source_types != derived_source_types:
            result.errors.append(
                "Invalid source_types: expected selected Asset source types in first-use order"
            )
        for forbidden in ("source_hash", "archived_ext", "copyright"):
            if forbidden in frontmatter:
                result.errors.append(
                    f"Invalid record/3 legacy authority projection: {forbidden}"
                )

    if "document_type" in frontmatter:
        document_type = frontmatter["document_type"]
        if not isinstance(document_type, str) or document_type not in DOCUMENT_TYPES:
            result.errors.append(
                "Invalid document_type: expected one of " + ", ".join(DOCUMENT_TYPES)
            )

    # Check body content
    body = parts[2].strip()
    if not body:
        result.warnings.append("No content after frontmatter (empty body)")
        return result

    for annotation in re.findall(r"<!--\s*message:\s*(\{.*?\})\s*-->", body):
        try:
            message = yaml.safe_load(annotation)
        except yaml.YAMLError:
            continue
        if isinstance(message, dict) and "date" in message:
            if not (
                (
                    isinstance(message["date"], str)
                    and is_rfc3339_instant(message["date"])
                )
                or (allow_legacy_temporal and isinstance(message["date"], str))
            ):
                result.errors.append(
                    "Invalid annotations.message.date: expected a quoted RFC 3339 "
                    "timestamp with Z or an explicit offset"
                )

    # Check for HTML tags
    html_tags = re.findall(r"<(sup|sub|br|div|span|p|b|i|em|strong)[>\s/]", body)
    if html_tags:
        unique_tags = sorted(set(html_tags))
        result.warnings.append(
            f"HTML tags found (should use markdown instead): "
            f"{', '.join('<' + t + '>' for t in unique_tags)}"
        )

    return result
