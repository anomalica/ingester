#!/usr/bin/env python3
"""Produce and migrate ``anomalica/record/3`` envelopes.

Format handlers still emit their source-specific, legacy-shaped intermediate
records.  The host pipeline calls this module after the exact acquired bytes
have been archived.  Asset/Selection policy and identity remain single-sourced
in ``anomalica-common``; this module supplies acquisition facts, preserves the
handler body, and performs the filesystem transition.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import yaml
from pydantic import ValidationError

from anomalica_common.identity import record_identity
from anomalica_common.pre_digest import (
    PreparedPageRecord,
    SourceMapError,
    prepare_page_record,
    store_source_map,
)
from anomalica_common.records import (
    AssetDescriptor,
    PageMapEntry,
    Record3Structure,
    RecordSelection,
)

try:
    from document_type import normalise_file_format
except ModuleNotFoundError:
    from shared.document_type import normalise_file_format


RECORD3_SCHEMA = "anomalica/record/3"
LEGACY_SCHEMAS = {"anomalica/record/1", "anomalica/record/2"}
IDENTITY_MAP_SCHEMA = "anomalica/record-identity-map/1"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_URL = re.compile(r"^https?://")
_BARE_TOKEN = re.compile(r"^[a-z0-9][a-z0-9+_-]*$")
_SNAPSHOT_ROLES = {"page_render", "single_file"}
_SNAPSHOT_EXTENSIONS = {
    "application/pdf": "pdf",
    "text/html": "html",
}
_SNAPSHOT_TRANSFORMS = {
    "page_render": "chromium-page-render-v1",
    "single_file": "single-file-cli-v1",
}

_PROVENANCE_FIELDS = {
    "collection": "collection",
    "publisher": "publisher",
    "creators": "creators",
    "date_published": "published_date",
    "posted_by": "posted_by",
    "posted_date": "posted_date",
    "container_title": "container_title",
    "source_url": "source_url",
    "also_published_at": "also_published_at",
    "description": "description",
    "audience": "audience",
    "disclosure": "disclosure",
}
_REMOVED_LEGACY_FIELDS = {
    "schema",
    "content_hash",
    "source_hash",
    "archived_ext",
    "pages",
    "copyright",
    "date_accessed",
    "fetched_url",
    "source_file",
    "source_id",
    "authors",
    "assets",
    "selection",
    "page_map",
    "source_types",
    "legacy_identities",
    "storage",
    *_PROVENANCE_FIELDS,
}


class Record3Error(ValueError):
    """A record/3 producer or migration invariant could not be established."""


@dataclass(frozen=True)
class RecordDocument:
    frontmatter: dict[str, Any]
    body: str
    raw: str


@dataclass(frozen=True)
class FinalisedRecord:
    record_path: Path
    content_hash: str
    asset_hash: str
    source_map_path: Path | None


@dataclass(frozen=True)
class MigrationResult:
    record_path: Path
    legacy_path: Path
    identity_map_path: Path
    content_hash: str
    moved_sidecars: tuple[Path, ...]


def _labelled_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def read_record(path: Path) -> RecordDocument:
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise Record3Error(f"record is not valid UTF-8: {path}") from exc
    if not raw.startswith("---\n"):
        raise Record3Error(f"record has no opening frontmatter fence: {path}")
    closing = raw.find("\n---\n", 4)
    if closing < 0:
        raise Record3Error(f"record has no closing frontmatter fence: {path}")
    try:
        frontmatter = yaml.safe_load(raw[4:closing])
    except yaml.YAMLError as exc:
        raise Record3Error(f"record frontmatter is invalid: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise Record3Error("record frontmatter must be a mapping")
    return RecordDocument(frontmatter, raw[closing + 5 :], raw)


def _record_text(frontmatter: Mapping[str, Any], body: str) -> str:
    encoded = yaml.safe_dump(
        dict(frontmatter),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).rstrip("\n")
    return f"---\n{encoded}\n---\n{body}"


def _non_empty(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _merge_field(target: dict[str, Any], key: str, value: Any, *, label: str) -> None:
    if value is None or value == [] or value == {}:
        return
    if key in target and target[key] != value:
        raise Record3Error(f"conflicting {label}.{key} values")
    target[key] = value


def _source_id_is_copy(
    frontmatter: Mapping[str, Any], source_id: str | None = None
) -> bool:
    source_id = _non_empty(source_id or frontmatter.get("source_id"))
    return source_id is not None and (
        source_id.startswith(
            ("youtube:", "dvids:", "url:", "vimeo:", "twitch:", "archiveorg:")
        )
    )


def _provenance(frontmatter: Mapping[str, Any]) -> dict[str, Any] | None:
    existing = frontmatter.get("provenance")
    if existing is None:
        provenance: dict[str, Any] = {}
    elif isinstance(existing, dict):
        provenance = dict(existing)
    else:
        raise Record3Error("provenance must be a mapping")

    creators = frontmatter.get("creators")
    if creators is None:
        creators = frontmatter.get("authors")
    values = dict(frontmatter)
    values["creators"] = creators
    for old, new in _PROVENANCE_FIELDS.items():
        _merge_field(provenance, new, values.get(old), label="provenance")

    source_id = _non_empty(frontmatter.get("source_id"))
    if source_id and not _source_id_is_copy(frontmatter):
        identifiers = provenance.get("identifiers")
        if identifiers is None:
            identifiers = {}
        if not isinstance(identifiers, dict):
            raise Record3Error("provenance.identifiers must be a mapping")
        identifiers = dict(identifiers)
        _merge_field(
            identifiers,
            "source_id",
            source_id,
            label="provenance.identifiers",
        )
        provenance["identifiers"] = identifiers
    return provenance or None


def _acquisition(
    frontmatter: Mapping[str, Any], manifest: Mapping[str, Any] | None
) -> dict[str, Any]:
    manifest = manifest or {}
    acquired_value = frontmatter.get("date_accessed") or manifest.get("fetched_at")
    try:
        acquired_at = (
            acquired_value
            if isinstance(acquired_value, datetime)
            else datetime.fromisoformat(str(acquired_value).replace("Z", "+00:00"))
        )
    except (TypeError, ValueError) as exc:
        raise Record3Error(
            "Asset acquisition requires an offset-bearing RFC 3339 acquired_at"
        ) from exc
    if acquired_at.tzinfo is None or acquired_at.utcoffset() is None:
        raise Record3Error("Asset acquisition requires acquired_at metadata")
    canonical_acquired_at = acquired_at.isoformat().replace("+00:00", "Z")
    acquisition: dict[str, Any] = {"acquired_at": canonical_acquired_at}

    fetched_url = (
        frontmatter.get("fetched_url")
        or manifest.get("fetched_url")
        or (
            manifest.get("source")
            if _non_empty(manifest.get("source"))
            and _URL.match(str(manifest["source"]))
            else None
        )
    )
    if _non_empty(fetched_url):
        acquisition["fetched_url"] = fetched_url

    source_file = frontmatter.get("source_file")
    if not source_file:
        source = manifest.get("source")
        if _non_empty(source) and not _URL.match(str(source)):
            # The manifest is private staging state. Only the filename of a
            # locally acquired copy belongs in the repository's Asset metadata.
            source_file = Path(str(source)).name
    if _non_empty(source_file):
        acquisition["source_file"] = source_file
    source_id = _non_empty(frontmatter.get("source_id") or manifest.get("source_id"))
    if source_id and _source_id_is_copy(frontmatter, source_id):
        acquisition["copy_identifiers"] = {"source_id": source_id}
    return acquisition


def _copyright(frontmatter: Mapping[str, Any]) -> dict[str, Any]:
    value = frontmatter.get("copyright")
    if not isinstance(value, dict) or not _non_empty(value.get("status")):
        raise Record3Error("Asset migration requires explicit copyright authority")
    return dict(value)


def _source_type(frontmatter: Mapping[str, Any]) -> str:
    value = _non_empty(frontmatter.get("source_type"))
    if value is None:
        raise Record3Error("record requires a source_type")
    return value


def _file_format(frontmatter: Mapping[str, Any], archived_ext: str) -> str:
    value = _non_empty(frontmatter.get("file_format"))
    if value:
        return value
    normalised = normalise_file_format(archived_ext)
    if not normalised:
        raise Record3Error("Asset file_format cannot be derived")
    return normalised


def _pages(frontmatter: Mapping[str, Any], source_type: str) -> int | None:
    if source_type not in {"pdf", "image"}:
        return None
    value = frontmatter.get("pages")
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise Record3Error(
            f"{source_type} Asset requires a positive physical page count"
        )
    if source_type == "image" and value != 1:
        raise Record3Error("a standalone image Asset must have exactly one page")
    return value


def _asset_descriptor(
    frontmatter: Mapping[str, Any],
    asset_path: Path,
    manifest: Mapping[str, Any] | None,
    *,
    expected_asset_hash: str | None = None,
) -> AssetDescriptor:
    if not asset_path.is_file():
        raise Record3Error(f"held Asset bytes are missing: {asset_path}")
    asset_hash = _labelled_sha256(asset_path)
    if expected_asset_hash is not None and asset_hash != expected_asset_hash:
        raise Record3Error(
            f"held Asset bytes hash to {asset_hash}, expected {expected_asset_hash}"
        )
    manifest_hash = _non_empty((manifest or {}).get("asset_hash"))
    if manifest_hash:
        manifest_hash = (
            manifest_hash
            if manifest_hash.startswith("sha256:")
            else f"sha256:{manifest_hash}"
        )
        if manifest_hash != asset_hash:
            raise Record3Error(
                "acquisition manifest asset_hash does not match held bytes"
            )

    archived_ext = asset_path.suffix.removeprefix(".").lower()
    if not archived_ext:
        raise Record3Error("held Asset has no archived extension")
    source_type = _source_type(frontmatter)
    value: dict[str, Any] = {
        "asset_hash": asset_hash,
        "file_format": _file_format(frontmatter, archived_ext),
        "archived_ext": archived_ext,
        "source_type": source_type,
        "acquisition": _acquisition(frontmatter, manifest),
        "copyright": _copyright(frontmatter),
    }
    pages = _pages(frontmatter, source_type)
    if pages is not None:
        value["pages"] = pages
    try:
        return AssetDescriptor.model_validate(value)
    except ValidationError as exc:
        raise Record3Error(f"invalid Asset descriptor: {exc}") from exc


def _page_map(asset: AssetDescriptor) -> list[PageMapEntry] | None:
    if asset.source_type not in {"pdf", "image"}:
        return None
    assert asset.pages is not None
    return [
        PageMapEntry(
            record_page=page,
            asset_hash=asset.asset_hash,
            asset_file_page=page,
        )
        for page in range(1, asset.pages + 1)
    ]


def _processing(frontmatter: Mapping[str, Any], asset_hash: str, source_type: str):
    value = frontmatter.get("processing")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise Record3Error("processing must be a mapping")
    processing = dict(value)
    pipeline_version = processing.pop("pipeline_version", None)
    asset_versions = processing.get("asset_pipeline_versions")
    if pipeline_version is not None:
        if (
            not isinstance(pipeline_version, int)
            or isinstance(pipeline_version, bool)
            or pipeline_version <= 0
        ):
            raise Record3Error("processing.pipeline_version must be a positive integer")
        expected = [
            {
                "asset_hash": asset_hash,
                "source_type": source_type,
                "pipeline_version": pipeline_version,
            }
        ]
        if asset_versions is not None and asset_versions != expected:
            raise Record3Error("processing Asset pipeline versions conflict")
        processing["asset_pipeline_versions"] = expected
    return processing


def _snapshots(
    frontmatter: Mapping[str, Any],
    manifest: Mapping[str, Any] | None,
    staging_dir: Path | None,
    primary: AssetDescriptor,
) -> list[dict[str, Any]] | None:
    manifest_snapshots = (manifest or {}).get("snapshots")
    if not manifest_snapshots:
        # An already-finalised record retains its complete descriptors.
        current = frontmatter.get("snapshots")
        return list(current) if isinstance(current, list) and current else None
    if not isinstance(manifest_snapshots, list) or staging_dir is None:
        raise Record3Error("snapshot manifest is malformed or has no staging directory")
    result: list[dict[str, Any]] = []
    snapshot_acquisition = _acquisition(frontmatter, manifest)
    for entry in manifest_snapshots:
        if not isinstance(entry, dict):
            raise Record3Error("snapshot manifest entry must be a mapping")
        relative = _non_empty(entry.get("path"))
        role = _non_empty(entry.get("role"))
        extension = _non_empty(entry.get("extension"))
        if not relative or not role or not extension:
            raise Record3Error("snapshot requires path, role and extension")
        if role not in _SNAPSHOT_ROLES:
            raise Record3Error(f"unsupported snapshot role: {role}")
        staging_root = staging_dir.resolve()
        path = (staging_root / relative).resolve()
        try:
            path.relative_to(staging_root)
        except ValueError as exc:
            raise Record3Error("snapshot path escapes the staging directory") from exc
        if not path.is_file():
            raise Record3Error(f"held snapshot bytes are missing: {path}")
        snapshot_hash = _labelled_sha256(path)
        declared = _non_empty(entry.get("hash"))
        if declared:
            declared = (
                declared if declared.startswith("sha256:") else f"sha256:{declared}"
            )
            if declared != snapshot_hash:
                raise Record3Error("snapshot manifest hash does not match held bytes")
        descriptor = AssetDescriptor.model_validate(
            {
                "asset_hash": snapshot_hash,
                "file_format": normalise_file_format(extension) or extension.lower(),
                "archived_ext": extension.lower(),
                "source_type": primary.source_type,
                **({"pages": 1} if role == "page_render" else {}),
                "acquisition": snapshot_acquisition,
                "copyright": primary.copyright.model_dump(
                    mode="json", exclude_none=True
                ),
                "derived_from": {
                    "asset_hash": primary.asset_hash,
                    "transform": _SNAPSHOT_TRANSFORMS[role],
                },
            }
        )
        result.append(
            {
                "role": role,
                "asset": descriptor.model_dump(mode="json", exclude_none=True),
            }
        )
    return result or None


def validate_record3_snapshots(frontmatter: Mapping[str, Any]) -> None:
    """Validate the complete derivative-Asset shape omitted by the base model."""
    raw = frontmatter.get("snapshots")
    if raw is None:
        return
    if not isinstance(raw, list) or not raw:
        raise Record3Error("snapshots must be a non-empty list when present")
    selected_assets = {
        item.get("asset_hash")
        for item in frontmatter.get("assets", [])
        if isinstance(item, dict)
    }
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"role", "asset"}:
            raise Record3Error("snapshot entry must be exactly {role, asset}")
        if item.get("role") not in _SNAPSHOT_ROLES:
            raise Record3Error(f"unsupported snapshot role: {item.get('role')}")
        try:
            descriptor = AssetDescriptor.model_validate(item.get("asset"))
        except ValidationError as exc:
            raise Record3Error(f"invalid snapshot Asset descriptor: {exc}") from exc
        if descriptor.derived_from is None:
            raise Record3Error("snapshot Asset requires derived_from lineage")
        if descriptor.derived_from.asset_hash not in selected_assets:
            raise Record3Error("snapshot lineage must name one of the Record Assets")
        role = item["role"]
        if descriptor.derived_from.transform != _SNAPSHOT_TRANSFORMS[role]:
            raise Record3Error("snapshot lineage transform disagrees with its role")
        expected_format = "pdf" if role == "page_render" else "html"
        if (
            descriptor.file_format != expected_format
            or descriptor.archived_ext != expected_format
        ):
            raise Record3Error(f"{role} snapshot must be {expected_format}")
        if role == "page_render" and descriptor.pages != 1:
            raise Record3Error("page_render snapshot must have exactly one page")


def _legacy_snapshot_manifest(
    frontmatter: Mapping[str, Any], records_dir: Path
) -> dict[str, Any] | None:
    raw = frontmatter.get("snapshots")
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw:
        raise Record3Error("legacy snapshots must be a non-empty list when present")
    entries: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise Record3Error("legacy snapshot entry must be a mapping")
        role = _non_empty(item.get("role"))
        snapshot_hash = _non_empty(item.get("hash"))
        if (
            role not in _SNAPSHOT_ROLES
            or not snapshot_hash
            or not _SHA256.fullmatch(snapshot_hash)
        ):
            raise Record3Error("legacy snapshot has an invalid role or hash")
        bare = snapshot_hash.removeprefix("sha256:")
        extension = _SNAPSHOT_EXTENSIONS.get(_non_empty(item.get("content_type")) or "")
        candidates = []
        if extension:
            declared = records_dir / f"{bare}.{extension}"
            if declared.is_file():
                candidates.append(declared)
        if not candidates:
            candidates = [
                path
                for path in records_dir.glob(f"{bare}.*")
                if path.is_file() and path.stem == bare
            ]
        if len(candidates) != 1:
            raise Record3Error(
                f"legacy snapshot archive does not resolve exactly once: {snapshot_hash}"
            )
        held = candidates[0]
        entries.append(
            {
                "path": held.name,
                "extension": held.suffix.removeprefix(".").lower(),
                "role": role,
                "hash": snapshot_hash,
            }
        )
    return {"snapshots": entries}


def build_default_record3(
    frontmatter: Mapping[str, Any],
    body: str,
    asset_path: Path,
    manifest: Mapping[str, Any] | None = None,
    *,
    staging_dir: Path | None = None,
    legacy_identity: bool = False,
    expected_asset_hash: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Build the one whole-Asset default Record from exact held bytes."""
    schema = frontmatter.get("schema")
    if schema not in LEGACY_SCHEMAS:
        raise Record3Error(f"cannot convert unsupported record schema: {schema!r}")
    title = _non_empty(frontmatter.get("title"))
    if title is None:
        raise Record3Error("record title is missing")

    asset = _asset_descriptor(
        frontmatter, asset_path, manifest, expected_asset_hash=expected_asset_hash
    )
    selection = [{"asset_hash": asset.asset_hash, "selector": {"type": "whole"}}]
    content_hash = record_identity(selection)
    page_map = _page_map(asset)
    structure = RecordSelection.model_validate(
        {
            "assets": [asset.model_dump(mode="json", exclude_none=True)],
            "selection": selection,
            **(
                {"page_map": [item.model_dump(mode="json") for item in page_map]}
                if page_map is not None
                else {}
            ),
        }
    )

    result: dict[str, Any] = {
        "schema": RECORD3_SCHEMA,
        "content_hash": content_hash,
        "title": title,
        "source_types": [asset.source_type],
        # These homogeneous/single-Asset projections remain permitted while
        # concrete readers migrate; authority stays in assets[].
        "source_type": asset.source_type,
        "file_format": asset.file_format,
        "assets": [asset.model_dump(mode="json", exclude_none=True)],
        "selection": structure.selection.model_dump(mode="json"),
    }
    if page_map is not None:
        result["page_map"] = [item.model_dump(mode="json") for item in page_map]

    provenance = _provenance(frontmatter)
    if provenance:
        result["provenance"] = provenance
    for key, value in frontmatter.items():
        if key in _REMOVED_LEGACY_FIELDS or key == "processing" or key == "snapshots":
            continue
        result[key] = value
    processing = _processing(frontmatter, asset.asset_hash, str(asset.source_type))
    if processing:
        result["processing"] = processing
    snapshots = _snapshots(frontmatter, manifest, staging_dir, asset)
    if snapshots:
        result["snapshots"] = snapshots
    if legacy_identity:
        result["legacy_identities"] = [
            {"schema": schema, "content_hash": frontmatter.get("content_hash")}
        ]

    try:
        Record3Structure.from_frontmatter(result)
    except ValidationError as exc:
        raise Record3Error(f"invalid record/3 structure: {exc}") from exc
    validate_record3_snapshots(result)
    return result, _record_text(result, body)


def _load_manifest(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Record3Error(f"acquisition manifest is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise Record3Error("acquisition manifest must be a mapping")
    return value


def _atomic_write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    binary = isinstance(data, bytes)
    with tempfile.NamedTemporaryFile(
        mode="wb" if binary else "w",
        encoding=None if binary else "utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as temporary:
        temporary.write(data)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def _repoint_aliases(by_name: Path, old_path: Path, new_path: Path) -> None:
    if not by_name.is_dir() or old_path == new_path:
        return
    old_resolved = old_path.resolve(strict=False)
    for alias in by_name.iterdir():
        if not alias.is_symlink():
            continue
        try:
            target = (alias.parent / os.readlink(alias)).resolve(strict=False)
        except OSError:
            continue
        if target != old_resolved:
            continue
        alias.unlink()
        alias.symlink_to(os.path.relpath(new_path, alias.parent))


def _move_media(output_dir: Path, old_stem: str, new_stem: str) -> None:
    old = output_dir / "media" / old_stem
    new = output_dir / "media" / new_stem
    if not old.is_dir() or old == new:
        return
    new.mkdir(parents=True, exist_ok=True)
    for source in old.iterdir():
        target = new / source.name
        if target.exists():
            if (
                not source.is_file()
                or not target.is_file()
                or source.read_bytes() != target.read_bytes()
            ):
                raise Record3Error(f"media collision at {target}")
            source.unlink()
        else:
            source.replace(target)
    old.rmdir()


def _validate_media_move(output_dir: Path, old_stem: str, new_stem: str) -> None:
    old = output_dir / "media" / old_stem
    new = output_dir / "media" / new_stem
    if not old.is_dir() or old == new or not new.exists():
        return
    for source in old.iterdir():
        target = new / source.name
        if target.exists() and (
            not source.is_file()
            or not target.is_file()
            or source.read_bytes() != target.read_bytes()
        ):
            raise Record3Error(f"media collision at {target}")


def _prepare_source_map(
    frontmatter: Mapping[str, Any], body: str, *, required: bool
) -> PreparedPageRecord | None:
    if "page_map" not in frontmatter:
        return None
    try:
        prepared = prepare_page_record(frontmatter, body)
    except (SourceMapError, ValidationError) as exc:
        if required:
            raise Record3Error(
                f"cannot produce preparation-version-9 source map: {exc}"
            ) from exc
        return None
    return prepared


def _assert_source_map_writable(
    output_dir: Path, prepared: PreparedPageRecord | None
) -> None:
    if prepared is None:
        return
    target = (
        output_dir
        / "source-maps"
        / f"{prepared.source_map_sha256.removeprefix('sha256:')}.json"
    )
    if target.exists() and target.read_bytes() != prepared.source_map_json:
        raise Record3Error(f"source-map hash collision at {target}")


def _store_prepared_source_map(
    output_dir: Path, prepared: PreparedPageRecord | None
) -> Path | None:
    return (
        store_source_map(output_dir / "source-maps", prepared)
        if prepared is not None
        else None
    )


def finalise_handler_record(
    record_path: Path,
    asset_path: Path,
    manifest_path: Path,
    output_dir: Path,
) -> FinalisedRecord:
    """Finalise one ordinary successful acquisition as one whole-Asset record/3."""
    document = read_record(record_path)
    manifest = _load_manifest(manifest_path)
    if document.frontmatter.get("schema") == RECORD3_SCHEMA:
        if document.frontmatter.get("superseded_by") or document.frontmatter.get(
            "retired_into"
        ):
            raise Record3Error("ordinary refresh cannot revive a retired Record")
        try:
            structure = Record3Structure.from_frontmatter(document.frontmatter)
        except ValidationError as exc:
            raise Record3Error(f"existing record/3 is invalid: {exc}") from exc
        validate_record3_snapshots(document.frontmatter)
        actual = _labelled_sha256(asset_path)
        whole = structure.selection.root
        if (
            len(structure.assets) != 1
            or len(whole) != 1
            or whole[0].selector.type != "whole"
            or structure.assets[0].asset_hash != actual
        ):
            raise Record3Error(
                "ordinary refresh does not target one matching whole Asset"
            )
        frontmatter = dict(document.frontmatter)
        # Storage is an operational pointer derived after final rights merging.
        # Never carry a stale zone/key through a refresh if the backup step fails.
        frontmatter.pop("storage", None)
        content = _record_text(frontmatter, document.body)
        asset_hash = actual
        if manifest and manifest.get("snapshots"):
            snapshots = _snapshots(
                frontmatter, manifest, manifest_path.parent, structure.assets[0]
            )
            if snapshots:
                frontmatter["snapshots"] = snapshots
            else:
                frontmatter.pop("snapshots", None)
            validate_record3_snapshots(frontmatter)
            content = _record_text(frontmatter, document.body)
    else:
        frontmatter, content = build_default_record3(
            document.frontmatter,
            document.body,
            asset_path,
            manifest,
            staging_dir=manifest_path.parent,
        )
        asset_hash = frontmatter["assets"][0]["asset_hash"]

    content_hash = frontmatter["content_hash"]
    bare = content_hash.removeprefix("sha256:")
    target = output_dir / "store" / f"{bare}.md"
    if target.exists() and target != record_path:
        existing = read_record(target)
        if existing.frontmatter.get("schema") != RECORD3_SCHEMA:
            raise Record3Error(
                f"Record identity target is occupied by legacy data: {target}"
            )
        if existing.frontmatter.get("superseded_by") or existing.frontmatter.get(
            "retired_into"
        ):
            raise Record3Error(
                "Record identity target is retired and cannot be replaced"
            )
        try:
            current = Record3Structure.from_frontmatter(existing.frontmatter)
        except ValidationError as exc:
            raise Record3Error(f"Record identity target is invalid: {exc}") from exc
        if current.content_hash != content_hash:
            raise Record3Error("Record identity target collision")

    prepared = _prepare_source_map(frontmatter, document.body, required=True)
    _assert_source_map_writable(output_dir, prepared)
    _atomic_write(target, content)
    old_stem = record_path.name.removesuffix(".md")
    new_stem = target.stem
    if record_path != target:
        record_path.unlink(missing_ok=True)
        old_verification = record_path.parent / f"{old_stem}.verification.json"
        if old_verification.exists():
            old_verification.replace(target.parent / f"{new_stem}.verification.json")
    _move_media(output_dir, old_stem, new_stem)
    _repoint_aliases(output_dir / "by-name", record_path, target)
    source_map_path = _store_prepared_source_map(output_dir, prepared)
    return FinalisedRecord(target, content_hash, asset_hash, source_map_path)


def _implicit_asset_hash(frontmatter: Mapping[str, Any]) -> str:
    value = frontmatter.get("source_hash") or frontmatter.get("content_hash")
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise Record3Error("legacy Record has no canonical implicit Asset hash")
    return value


def _identity_map(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise Record3Error(f"record identity map is invalid: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != IDENTITY_MAP_SCHEMA:
        raise Record3Error("record identity map has an unsupported schema")
    entries = value.get("entries")
    if not isinstance(entries, list):
        raise Record3Error("record identity map entries must be a list")
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "old_schema",
            "old_content_hash",
            "new_content_hash",
        }:
            raise Record3Error("record identity map entry has the wrong shape")
        old = entry.get("old_content_hash")
        new = entry.get("new_content_hash")
        if (
            entry.get("old_schema") not in LEGACY_SCHEMAS
            or not isinstance(old, str)
            or not _SHA256.fullmatch(old)
            or not isinstance(new, str)
            or not _SHA256.fullmatch(new)
        ):
            raise Record3Error("record identity map entry has an invalid identity")
        if old in seen:
            raise Record3Error("record identity map has a duplicate old hash")
        seen.add(old)
    if entries != sorted(entries, key=lambda item: item["old_content_hash"]):
        raise Record3Error("record identity map entries are not canonically sorted")
    return [dict(entry) for entry in entries]


def _assert_no_selection_collapse(store: Path, source: Path, asset_hash: str) -> None:
    for candidate in sorted(store.rglob("*.md")):
        if candidate == source:
            continue
        if "legacy-identities" in candidate.relative_to(store).parts:
            continue
        try:
            frontmatter = read_record(candidate).frontmatter
        except Record3Error:
            continue
        if frontmatter.get("superseded_by") or frontmatter.get("retired_into"):
            continue
        schema = frontmatter.get("schema")
        if schema in LEGACY_SCHEMAS:
            try:
                other_hash = _implicit_asset_hash(frontmatter)
            except Record3Error:
                continue
            if other_hash == asset_hash:
                raise Record3Error(
                    "several live legacy Records collapse to one whole-Asset Selection"
                )
        elif schema == RECORD3_SCHEMA:
            selection = frontmatter.get("selection")
            if selection == [{"asset_hash": asset_hash, "selector": {"type": "whole"}}]:
                raise Record3Error("the canonical whole-Asset Selection already exists")


def migrate_legacy_record(
    record_path: Path,
    records_dir: Path,
    *,
    output_dir: Path | None = None,
) -> MigrationResult:
    """Migrate one live legacy envelope from held bytes, without reacquisition.

    All validation and collision checks complete before the first write.  Human
    and derived sidecars move beside the old envelope as audit history and are
    never copied to the new Record identity.
    """
    output_dir = output_dir or record_path.parent.parent
    store = output_dir / "store"
    try:
        relative_input = record_path.relative_to(store)
    except ValueError as exc:
        raise Record3Error("migration input is not in the live store") from exc
    if len(relative_input.parts) != 1:
        raise Record3Error("migration input is not a top-level live Record")
    document = read_record(record_path)
    schema = document.frontmatter.get("schema")
    if schema not in LEGACY_SCHEMAS:
        raise Record3Error("migration input is not record/1 or record/2")
    if document.frontmatter.get("superseded_by") or document.frontmatter.get(
        "retired_into"
    ):
        raise Record3Error("migration input is retired, not a live legacy Record")
    old_hash = document.frontmatter.get("content_hash")
    if not isinstance(old_hash, str) or not _SHA256.fullmatch(old_hash):
        raise Record3Error("legacy content_hash must be a full lowercase SHA-256")
    asset_hash = _implicit_asset_hash(document.frontmatter)
    archived_ext = _non_empty(document.frontmatter.get("archived_ext"))
    if not archived_ext or not _BARE_TOKEN.fullmatch(archived_ext):
        raise Record3Error("legacy Record has no valid archived_ext")
    asset_path = records_dir / f"{asset_hash.removeprefix('sha256:')}.{archived_ext}"
    if not asset_path.is_file():
        raise Record3Error(f"held archive is missing: {asset_path}")
    if _labelled_sha256(asset_path) != asset_hash:
        raise Record3Error("held archive bytes do not match the implicit Asset hash")

    _assert_no_selection_collapse(store, record_path, asset_hash)
    snapshot_manifest = _legacy_snapshot_manifest(document.frontmatter, records_dir)
    frontmatter, content = build_default_record3(
        document.frontmatter,
        document.body,
        asset_path,
        snapshot_manifest,
        staging_dir=records_dir,
        legacy_identity=True,
        expected_asset_hash=asset_hash,
    )
    new_hash = frontmatter["content_hash"]
    new_path = store / f"{new_hash.removeprefix('sha256:')}.md"
    if new_path.exists() and new_path != record_path:
        raise Record3Error("new Record identity path already exists")

    map_path = store / "_record_identity_map.yaml"
    entries = _identity_map(map_path)
    if any(entry["old_content_hash"] == old_hash for entry in entries):
        raise Record3Error("legacy content_hash already has an identity-map entry")
    entries.append(
        {
            "old_schema": schema,
            "old_content_hash": old_hash,
            "new_content_hash": new_hash,
        }
    )
    entries.sort(key=lambda item: item["old_content_hash"])
    map_text = yaml.safe_dump(
        {"schema": IDENTITY_MAP_SCHEMA, "entries": entries},
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )

    schema_dir = "record-1" if schema.endswith("/1") else "record-2"
    legacy_dir = store / "legacy-identities" / schema_dir
    # record/2 envelopes conventionally carry a ``.v2.md`` filename while their
    # sidecars and media directory use the bare legacy content hash.  Resolve
    # authority from the declared identity rather than the envelope stem, or a
    # migration would strand exactly the review state it is required to retain
    # as audit history.
    legacy_storage_stem = old_hash.removeprefix("sha256:")
    sidecars = [record_path]
    sidecars.extend(
        path
        for path in sorted(record_path.parent.glob(f"{legacy_storage_stem}.*"))
        if path != record_path and path.is_file()
    )
    destinations: list[tuple[Path, Path]] = []
    for sidecar in sidecars:
        suffix = (
            ".md"
            if sidecar == record_path
            else sidecar.name[len(legacy_storage_stem) :]
        )
        destination = legacy_dir / f"{old_hash}{suffix}"
        if destination.exists():
            raise Record3Error(f"legacy migration destination exists: {destination}")
        destinations.append((sidecar, destination))

    _validate_media_move(output_dir, legacy_storage_stem, new_path.stem)
    # A migrated paged Record has the same source-map obligation as a newly
    # produced one. If its legacy body cannot be mapped exactly, leave the
    # envelope untouched for deterministic repair rather than publishing a
    # record/3 that downstream evidence code cannot anchor.
    prepared = _prepare_source_map(frontmatter, document.body, required=True)
    _assert_source_map_writable(output_dir, prepared)

    legacy_dir.mkdir(parents=True, exist_ok=True)
    for source, destination in destinations:
        source.replace(destination)
    _atomic_write(new_path, content)
    _atomic_write(map_path, map_text)
    _move_media(output_dir, legacy_storage_stem, new_path.stem)
    _repoint_aliases(output_dir / "by-name", record_path, new_path)
    _store_prepared_source_map(output_dir, prepared)
    return MigrationResult(
        record_path=new_path,
        legacy_path=next(
            destination for source, destination in destinations if source == record_path
        ),
        identity_map_path=map_path,
        content_hash=new_hash,
        moved_sidecars=tuple(destination for _, destination in destinations),
    )


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    finalise = subparsers.add_parser("finalise")
    finalise.add_argument("--record", type=Path, required=True)
    finalise.add_argument("--asset", type=Path, required=True)
    finalise.add_argument("--manifest", type=Path, required=True)
    finalise.add_argument("--output-dir", type=Path, required=True)
    migrate = subparsers.add_parser("migrate")
    migrate.add_argument("--record", type=Path, required=True)
    migrate.add_argument("--records-dir", type=Path, required=True)
    migrate.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "finalise":
            result = finalise_handler_record(
                args.record, args.asset, args.manifest, args.output_dir
            )
            print(result.record_path.relative_to(args.output_dir).as_posix())
        else:
            result = migrate_legacy_record(
                args.record, args.records_dir, output_dir=args.output_dir
            )
            print(result.record_path)
    except (OSError, Record3Error, ValidationError) as exc:
        print(f"Error: record/3 {args.command} failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
