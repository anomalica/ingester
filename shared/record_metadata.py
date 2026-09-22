"""Validated intake metadata hand-off for every ingest format."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import yaml

try:
    from dates import date_alias, is_evidenced_date
    from record import symlink_name
    from validator import validate
except ModuleNotFoundError:
    from shared.dates import date_alias, is_evidenced_date
    from shared.record import symlink_name
    from shared.validator import validate


SCHEMA = "anomalica/record-metadata/1"
ALLOWED_FIELDS = frozenset(
    {
        "title",
        "source_type",
        "source_url",
        "source_id",
        "publisher",
        "date_published",
        "description",
        "copyright",
    }
)
SOURCE_TYPES = frozenset({"pdf", "audio", "video", "web", "ebook", "image"})
COPYRIGHT_STATUSES = frozenset(
    {
        "public_domain",
        "open_licence",
        "publicly_accessible",
        "licensed",
        "restricted",
    }
)
IDENTITY_FIELDS = ("source_type", "source_url", "source_id")


class MetadataError(ValueError):
    """The supplied metadata cannot safely be applied to a record."""


def load_metadata(path: Path) -> dict:
    """Load and validate an allowlisted Scheduler metadata snapshot."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MetadataError(f"record metadata is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema", "metadata"}:
        raise MetadataError("record metadata must contain only schema and metadata")
    if payload.get("schema") != SCHEMA:
        raise MetadataError("unsupported record metadata schema")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict) or not metadata:
        raise MetadataError("record metadata must be a non-empty mapping")
    unknown = set(metadata) - ALLOWED_FIELDS
    if unknown:
        raise MetadataError(
            "record metadata contains unsupported fields: " + ", ".join(sorted(unknown))
        )

    for field in ("title", "source_url", "source_id", "publisher", "description"):
        if field in metadata and (
            not isinstance(metadata[field], str) or not metadata[field].strip()
        ):
            raise MetadataError(f"record metadata {field} must be a non-empty string")
    source_type = metadata.get("source_type")
    if source_type is not None and source_type not in SOURCE_TYPES:
        raise MetadataError("record metadata source_type is unsupported")
    source_url = metadata.get("source_url")
    if source_url is not None:
        parsed = urlparse(source_url)
        if parsed.scheme not in {"http", "https", "file"}:
            raise MetadataError("record metadata source_url must be an absolute URL")
    published = metadata.get("date_published")
    if published is not None and not (
        isinstance(published, str) and is_evidenced_date(published)
    ):
        raise MetadataError("record metadata date_published is not an evidenced date")
    copyright_block = metadata.get("copyright")
    if copyright_block is not None:
        if not isinstance(copyright_block, dict) or set(copyright_block) != {"status"}:
            raise MetadataError("record metadata copyright must contain only status")
        if copyright_block["status"] not in COPYRIGHT_STATUSES:
            raise MetadataError("record metadata copyright.status is unsupported")
    return metadata


def merge_manifest(manifest_path: Path, metadata_path: Path) -> None:
    """Put trusted source metadata into the acquisition manifest before dispatch."""
    metadata = load_metadata(metadata_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MetadataError(f"acquisition manifest is unreadable: {exc}") from exc
    if not isinstance(manifest, dict):
        raise MetadataError("acquisition manifest must be a mapping")

    for field in ("source_url", "source_id"):
        existing = manifest.get(field)
        supplied = metadata.get(field)
        if existing and supplied and existing != supplied:
            raise MetadataError(
                f"record metadata {field} conflicts with acquisition manifest"
            )
    for field in (
        "title",
        "source_url",
        "source_id",
        "publisher",
        "date_published",
        "description",
    ):
        if field in metadata:
            manifest[field] = metadata[field]
    if "source_type" in metadata:
        manifest["original_type"] = metadata["source_type"]
    if "copyright" in metadata:
        manifest["copyright_status"] = metadata["copyright"]["status"]
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _record_parts(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise MetadataError("record has no YAML frontmatter")
    parts = text.split("---", 2)
    if len(parts) != 3:
        raise MetadataError("record has incomplete YAML frontmatter")
    try:
        frontmatter = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        raise MetadataError(f"record frontmatter is invalid: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise MetadataError("record frontmatter must be a mapping")
    return frontmatter, parts[2]


def _reconcile_alias(record_path: Path, by_name_dir: Path, frontmatter: dict) -> None:
    by_name_dir.mkdir(parents=True, exist_ok=True)
    variant = ".v2" if record_path.name.endswith(".v2.md") else ""
    alias_date = (
        date_alias(
            frontmatter.get("date_published")
            or frontmatter.get("posted_date")
            or frontmatter.get("date_accessed")
        )
        or "undated"
    )
    desired = by_name_dir / symlink_name(
        alias_date,
        frontmatter["source_type"],
        frontmatter["title"],
        variant=variant,
    )
    target = record_path.resolve()
    if desired.exists() or desired.is_symlink():
        if not desired.is_symlink() or desired.resolve() != target:
            raise MetadataError(f"record metadata alias collides with {desired.name}")

    aliases = []
    for candidate in by_name_dir.iterdir():
        if not candidate.is_symlink():
            continue
        try:
            if candidate.resolve() == target:
                aliases.append(candidate)
        except OSError:
            continue
    for alias in aliases:
        if alias != desired:
            alias.unlink()
    if not desired.is_symlink():
        desired.symlink_to(os.path.relpath(record_path, by_name_dir))


def merge_record(record_path: Path, metadata_path: Path, by_name_dir: Path) -> None:
    """Merge trusted intake fields into one handler output and validate it."""
    metadata = load_metadata(metadata_path)
    frontmatter, body = _record_parts(record_path)
    for field in IDENTITY_FIELDS:
        existing = frontmatter.get(field)
        supplied = metadata.get(field)
        if existing and supplied and existing != supplied:
            raise MetadataError(
                f"record metadata {field} conflicts with handler output"
            )

    for field, value in metadata.items():
        if field == "copyright":
            existing = frontmatter.get("copyright")
            copyright_block = dict(existing) if isinstance(existing, dict) else {}
            copyright_block["status"] = value["status"]
            frontmatter[field] = copyright_block
        else:
            frontmatter[field] = value

    content = (
        "---\n"
        + yaml.safe_dump(
            frontmatter, default_flow_style=False, sort_keys=False, allow_unicode=True
        ).strip()
        + "\n---"
        + body
    )
    result = validate(content, expected_schema=str(frontmatter.get("schema") or ""))
    if result.errors:
        raise MetadataError(
            "record is invalid after metadata merge: " + "; ".join(result.errors)
        )

    _reconcile_alias(record_path, by_name_dir, frontmatter)
    record_path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    manifest = sub.add_parser("manifest")
    manifest.add_argument("--manifest", type=Path, required=True)
    manifest.add_argument("--metadata-file", type=Path, required=True)
    record = sub.add_parser("record")
    record.add_argument("--record", type=Path, required=True)
    record.add_argument("--by-name-dir", type=Path, required=True)
    record.add_argument("--metadata-file", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "manifest":
            merge_manifest(args.manifest, args.metadata_file)
        else:
            merge_record(args.record, args.metadata_file, args.by_name_dir)
    except (MetadataError, OSError) as exc:
        print(f"Error: cannot apply record metadata: {exc}", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
