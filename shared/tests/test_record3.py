import hashlib
import json
import os

import pytest
import yaml

from anomalica_common.identity import record_identity
from anomalica_common.pre_digest import source_map_hash
from audit_sources import _status, archived_assets, original_of, storage_key
from record3 import (
    Record3Error,
    build_default_record3,
    finalise_handler_record,
    migrate_legacy_record,
    read_record,
)
from verification import needs_sidecar


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _legacy(
    asset_hash: str,
    *,
    schema: str = "anomalica/record/1",
    content_hash: str | None = None,
    source_hash: bool = False,
    source_type: str = "pdf",
    pages: int | None = 2,
) -> str:
    frontmatter = {
        "schema": schema,
        "title": "Fixture document",
        "source_type": source_type,
        "file_format": "pdf" if source_type == "pdf" else "html",
        "content_hash": content_hash or asset_hash,
        "date_accessed": "2026-09-22T09:00:00Z",
        "source_url": "https://example.test/work",
        "fetched_url": "https://cdn.example.test/copy",
        "source_id": "fixture:1",
        "copyright": {"status": "licensed", "detail": "fixture authority"},
        "processing": {"handler": source_type, "pipeline_version": 3},
    }
    if source_hash:
        frontmatter["source_hash"] = asset_hash
    if pages is not None:
        frontmatter["pages"] = pages
    body = (
        "<!-- file_page: 1 -->\nAlpha page.\n<!-- file_page: 2 -->\nSecond page.\n"
        if source_type == "pdf"
        else "Ordinary body.\n"
    )
    return (
        "---\n"
        + yaml.safe_dump(frontmatter, sort_keys=False).rstrip()
        + "\n---\n"
        + body
    )


def test_default_record_uses_exact_asset_bytes_and_shared_identity(tmp_path):
    data = b"%PDF-1.7 exact acquired bytes"
    asset = tmp_path / "asset.pdf"
    asset.write_bytes(data)
    asset_hash = _sha(data)
    document_path = tmp_path / "legacy.md"
    document_path.write_text(_legacy(asset_hash))
    document = read_record(document_path)

    frontmatter, content = build_default_record3(
        document.frontmatter,
        document.body,
        asset,
        {
            "asset_hash": asset_hash.removeprefix("sha256:"),
            "fetched_at": "2026-09-23T08:00:00+09:00",
            "source": "https://example.test/work",
        },
    )

    selection = [{"asset_hash": asset_hash, "selector": {"type": "whole"}}]
    assert frontmatter["schema"] == "anomalica/record/3"
    assert frontmatter["content_hash"] == record_identity(selection)
    assert frontmatter["assets"] == [
        {
            "asset_hash": asset_hash,
            "file_format": "pdf",
            "archived_ext": "pdf",
            "source_type": "pdf",
            "pages": 2,
            "acquisition": {
                "acquired_at": "2026-09-22T09:00:00Z",
                "fetched_url": "https://cdn.example.test/copy",
            },
            "copyright": {
                "status": "licensed",
                "detail": "fixture authority",
            },
        }
    ]
    assert frontmatter["selection"] == selection
    assert frontmatter["page_map"] == [
        {"record_page": 1, "asset_hash": asset_hash, "asset_file_page": 1},
        {"record_page": 2, "asset_hash": asset_hash, "asset_file_page": 2},
    ]
    assert frontmatter["provenance"] == {
        "source_url": "https://example.test/work",
        "identifiers": {"source_id": "fixture:1"},
    }
    assert "copyright:" not in content.split("assets:", 1)[0]
    assert frontmatter["processing"]["asset_pipeline_versions"] == [
        {
            "asset_hash": asset_hash,
            "source_type": "pdf",
            "pipeline_version": 3,
        }
    ]
    assert "pipeline_version" not in {
        key for key in frontmatter["processing"] if key != "asset_pipeline_versions"
    }


def test_local_manifest_path_becomes_filename_not_asset_provenance(tmp_path):
    asset = tmp_path / "asset.pdf"
    asset.write_bytes(b"%PDF-1.7 local copy")
    path = tmp_path / "record.md"
    path.write_text(_legacy(_sha(asset.read_bytes())))
    document = read_record(path)
    frontmatter, content = build_default_record3(
        document.frontmatter,
        document.body,
        asset,
        {
            "source": "/home/reviewer/inbox/lecture.pdf",
            "asset_hash": _sha(asset.read_bytes()).removeprefix("sha256:"),
        },
    )
    assert frontmatter["assets"][0]["acquisition"]["source_file"] == "lecture.pdf"
    assert "/home/reviewer" not in content


def test_legacy_yaml_timestamp_is_normalised_to_rfc3339(tmp_path):
    asset = tmp_path / "asset.html"
    asset.write_bytes(b"held web bytes")
    asset_hash = _sha(asset.read_bytes())
    record_path = tmp_path / "legacy.md"
    record_path.write_text(
        _legacy(asset_hash, source_type="web", pages=None).replace(
            "date_accessed: '2026-09-22T09:00:00Z'",
            "date_accessed: 2026-09-22 09:00:00+00:00",
        )
    )
    document = read_record(record_path)

    frontmatter, _ = build_default_record3(document.frontmatter, document.body, asset)

    assert frontmatter["assets"][0]["acquisition"]["acquired_at"] == (
        "2026-09-22T09:00:00Z"
    )


def test_copy_identifier_is_not_promoted_to_work_provenance(tmp_path):
    data = b"audio bytes"
    asset = tmp_path / "asset.opus"
    asset.write_bytes(data)
    asset_hash = _sha(data)
    frontmatter = {
        "schema": "anomalica/record/2",
        "title": "Fixture recording",
        "source_type": "audio",
        "file_format": "opus",
        "content_hash": asset_hash,
        "date_accessed": "2026-09-22T09:00:00Z",
        "source_url": "https://www.youtube.com/watch?v=fixture",
        "source_id": "youtube:fixture",
        "copyright": {"status": "publicly_accessible"},
    }

    record, _ = build_default_record3(frontmatter, "Transcript.\n", asset)

    assert record["assets"][0]["acquisition"]["copy_identifiers"] == {
        "source_id": "youtube:fixture"
    }
    assert "identifiers" not in record["provenance"]


def test_web_snapshot_descriptors_hash_exact_staged_bytes(tmp_path):
    asset = tmp_path / "asset.html"
    asset.write_bytes(b"<html>source</html>")
    snapshot = tmp_path / "snapshot_0.pdf"
    snapshot.write_bytes(b"%PDF snapshot")
    asset_hash = _sha(asset.read_bytes())
    snapshot_hash = _sha(snapshot.read_bytes())
    frontmatter = {
        "schema": "anomalica/record/1",
        "title": "Fixture page",
        "source_type": "web",
        "file_format": "html",
        "content_hash": asset_hash,
        "date_accessed": "2026-09-22T09:00:00Z",
        "copyright": {"status": "publicly_accessible"},
    }

    record, _ = build_default_record3(
        frontmatter,
        "Page body.\n",
        asset,
        {
            "snapshots": [
                {
                    "path": snapshot.name,
                    "role": "page_render",
                    "extension": "pdf",
                    "hash": snapshot_hash.removeprefix("sha256:"),
                }
            ]
        },
        staging_dir=tmp_path,
    )

    descriptor = record["snapshots"][0]
    assert descriptor["role"] == "page_render"
    assert descriptor["asset"]["asset_hash"] == snapshot_hash
    assert descriptor["asset"]["derived_from"] == {
        "asset_hash": asset_hash,
        "transform": "chromium-page-render-v1",
    }
    assert descriptor["asset"]["pages"] == 1


def test_whole_image_gets_one_server_derived_page_and_v9_source_map(tmp_path):
    output = tmp_path / "ingests"
    store = output / "store"
    staging = tmp_path / "staging"
    store.mkdir(parents=True)
    staging.mkdir()
    asset = staging / "asset.png"
    asset.write_bytes(b"exact PNG bytes")
    asset_hash = _sha(asset.read_bytes())
    intermediate = store / f"{asset_hash.removeprefix('sha256:')}.md"
    intermediate.write_text(
        _legacy(asset_hash, source_type="image", pages=1)
        .replace("file_format: html", "file_format: png")
        .replace("Ordinary body.\n", "<!-- file_page: 1 -->\nImage text.\n")
    )
    manifest = staging / "manifest.json"
    manifest.write_text(json.dumps({"fetched_at": "2026-09-22T09:00:00Z"}))

    result = finalise_handler_record(intermediate, asset, manifest, output)

    record = read_record(result.record_path).frontmatter
    assert record["assets"][0]["pages"] == 1
    assert record["page_map"] == [
        {"record_page": 1, "asset_hash": asset_hash, "asset_file_page": 1}
    ]
    assert result.source_map_path is not None
    source_map = json.loads(result.source_map_path.read_text())
    assert source_map["entries"][0]["record_page"] == 1
    assert source_map["entries"][0]["asset_file_page"] == 1
    assert source_map["entries"][0]["asset_text_sha256"].startswith("sha256:")


@pytest.mark.parametrize(
    ("remove", "message"),
    [
        ("date_accessed", "acquisition requires"),
        ("copyright", "copyright authority"),
    ],
)
def test_default_record_fails_closed_without_asset_authority(tmp_path, remove, message):
    asset = tmp_path / "asset.html"
    asset.write_bytes(b"held web bytes")
    asset_hash = _sha(asset.read_bytes())
    record_path = tmp_path / "legacy.md"
    record_path.write_text(_legacy(asset_hash, source_type="web", pages=None))
    document = read_record(record_path)
    frontmatter = dict(document.frontmatter)
    frontmatter.pop(remove)

    with pytest.raises(Record3Error, match=message):
        build_default_record3(frontmatter, document.body, asset)


def test_default_record_fails_closed_without_held_asset_bytes(tmp_path):
    missing = tmp_path / "missing.pdf"
    record_path = tmp_path / "legacy.md"
    record_path.write_text(_legacy("sha256:" + "a" * 64))
    document = read_record(record_path)

    with pytest.raises(Record3Error, match="held Asset bytes are missing"):
        build_default_record3(document.frontmatter, document.body, missing)


def test_snapshot_path_cannot_escape_staging(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    asset = staging / "asset.html"
    asset.write_bytes(b"<html>source</html>")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF outside")
    frontmatter = {
        "schema": "anomalica/record/1",
        "title": "Fixture page",
        "source_type": "web",
        "file_format": "html",
        "content_hash": _sha(asset.read_bytes()),
        "date_accessed": "2026-09-22T09:00:00Z",
        "copyright": {"status": "publicly_accessible"},
    }

    with pytest.raises(Record3Error, match="escapes"):
        build_default_record3(
            frontmatter,
            "Page body.\n",
            asset,
            {
                "snapshots": [
                    {
                        "path": "../outside.pdf",
                        "role": "page_render",
                        "extension": "pdf",
                    }
                ]
            },
            staging_dir=staging,
        )


def test_asset_authority_drives_archive_storage_and_verification_consumers():
    asset_hash = "a" * 64
    frontmatter = (
        "schema: anomalica/record/3\n"
        "content_hash: sha256:" + "b" * 64 + "\n"
        "assets:\n"
        f"  - asset_hash: sha256:{asset_hash}\n"
        "    archived_ext: epub\n"
        "    copyright:\n"
        "      status: restricted\n"
        "selection:\n"
        f"  - asset_hash: sha256:{asset_hash}\n"
        "    selector: {type: whole}\n"
    )
    record = f"---\n{frontmatter}---\nBody.\n"

    assert original_of(frontmatter) == (asset_hash, "epub")
    assert storage_key(frontmatter, "epub") == f"sources/{asset_hash}.epub"
    assert _status(frontmatter) == "restricted"
    assert needs_sidecar(record)


def test_composite_record_has_no_synthetic_singular_original():
    first = "a" * 64
    second = "b" * 64
    fm = yaml.safe_dump(
        {
            "schema": "anomalica/record/3",
            "content_hash": f"sha256:{'c' * 64}",
            "assets": [
                {
                    "asset_hash": f"sha256:{first}",
                    "archived_ext": "pdf",
                    "source_type": "pdf",
                    "copyright": {"status": "public_domain"},
                },
                {
                    "asset_hash": f"sha256:{second}",
                    "archived_ext": "jpg",
                    "source_type": "image",
                    "copyright": {"status": "restricted"},
                },
            ],
        },
        sort_keys=False,
    )

    assert original_of(fm) == (None, None)
    assert storage_key(fm, "pdf") is None
    assert _status(fm) is None
    assert archived_assets(fm) == [
        (first, "pdf", "public_domain", "pdf"),
        (second, "jpg", "restricted", "image"),
    ]


def test_finalise_renames_record_sidecar_media_alias_and_writes_v9_map(tmp_path):
    output = tmp_path / "ingests"
    store = output / "store"
    by_name = output / "by-name"
    media = output / "media"
    staging = tmp_path / "staging"
    for directory in (store, by_name, media, staging):
        directory.mkdir(parents=True)
    data = b"%PDF-1.7 exact acquired bytes"
    asset = staging / "asset.pdf"
    asset.write_bytes(data)
    asset_hash = _sha(data)
    old_hash = asset_hash.removeprefix("sha256:")
    old_record = store / f"{old_hash}.md"
    old_record.write_text(_legacy(asset_hash))
    (store / f"{old_hash}.verification.json").write_text("{}\n")
    media_dir = media / old_hash
    media_dir.mkdir()
    (media_dir / "page.png").write_bytes(b"image")
    alias = by_name / "fixture.md"
    alias.symlink_to(os.path.relpath(old_record, by_name))
    manifest = staging / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "asset": "asset.pdf",
                "asset_hash": old_hash,
                "fetched_at": "2026-09-22T09:00:00Z",
            }
        )
    )

    result = finalise_handler_record(old_record, asset, manifest, output)

    expected = record_identity(
        [{"asset_hash": asset_hash, "selector": {"type": "whole"}}]
    )
    assert result.content_hash == expected
    assert result.record_path == store / f"{expected.removeprefix('sha256:')}.md"
    assert not old_record.exists()
    assert alias.resolve() == result.record_path
    assert result.record_path.with_suffix(".verification.json").exists()
    assert (media / result.record_path.stem / "page.png").read_bytes() == b"image"
    assert result.source_map_path is not None
    source_map = json.loads(result.source_map_path.read_text())
    assert source_map["record_hash"] == expected
    assert source_map["entries"][0]["asset_hash"] == asset_hash
    assert source_map["entries"][0]["asset_text_sha256"].startswith("sha256:")
    assert result.source_map_path.stem == source_map_hash(source_map).removeprefix(
        "sha256:"
    )


def test_finalise_page_mapped_record_fails_closed_without_exact_markers(tmp_path):
    output = tmp_path / "ingests"
    store = output / "store"
    staging = tmp_path / "staging"
    store.mkdir(parents=True)
    staging.mkdir()
    data = b"%PDF broken map"
    asset = staging / "asset.pdf"
    asset.write_bytes(data)
    asset_hash = _sha(data)
    record = store / f"{asset_hash.removeprefix('sha256:')}.md"
    record.write_text(_legacy(asset_hash).replace("file_page: 2", "file_page: 9"))
    manifest = staging / "manifest.json"
    manifest.write_text(
        json.dumps({"asset": "asset.pdf", "fetched_at": "2026-09-22T09:00:00Z"})
    )

    with pytest.raises(Record3Error, match="source map"):
        finalise_handler_record(record, asset, manifest, output)


def test_finalise_refreshes_web_snapshot_descriptors_from_new_exact_bytes(tmp_path):
    output = tmp_path / "ingests"
    store = output / "store"
    staging = tmp_path / "staging"
    store.mkdir(parents=True)
    staging.mkdir()
    asset = staging / "asset.html"
    asset.write_bytes(b"<html>source</html>")
    asset_hash = _sha(asset.read_bytes())
    frontmatter, content = build_default_record3(
        {
            "schema": "anomalica/record/1",
            "content_hash": asset_hash,
            "title": "Fixture page",
            "source_type": "web",
            "file_format": "html",
            "date_accessed": "2026-09-22T09:00:00Z",
            "copyright": {"status": "publicly_accessible"},
        },
        "Page body.\n",
        asset,
    )
    record = store / f"{frontmatter['content_hash'].removeprefix('sha256:')}.md"
    record.write_text(content)
    snapshot = staging / "snapshot_0.pdf"
    snapshot.write_bytes(b"%PDF refreshed snapshot")
    manifest = staging / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "fetched_at": "2026-09-23T10:30:00Z",
                "snapshots": [
                    {
                        "path": snapshot.name,
                        "role": "page_render",
                        "extension": "pdf",
                    }
                ],
            }
        )
    )

    finalise_handler_record(record, asset, manifest, output)

    refreshed = read_record(record).frontmatter
    descriptor = refreshed["snapshots"][0]["asset"]
    assert descriptor["asset_hash"] == _sha(snapshot.read_bytes())
    assert descriptor["acquisition"]["acquired_at"] == "2026-09-23T10:30:00Z"
    assert descriptor["derived_from"] == {
        "asset_hash": asset_hash,
        "transform": "chromium-page-render-v1",
    }


def test_finalise_refuses_to_overwrite_retired_selection_identity(tmp_path):
    output = tmp_path / "ingests"
    store = output / "store"
    staging = tmp_path / "staging"
    store.mkdir(parents=True)
    staging.mkdir()
    asset = staging / "asset.pdf"
    asset.write_bytes(b"%PDF retired")
    asset_hash = _sha(asset.read_bytes())
    intermediate = store / f"{asset_hash.removeprefix('sha256:')}.md"
    intermediate.write_text(_legacy(asset_hash))
    document = read_record(intermediate)
    retired_frontmatter, retired_content = build_default_record3(
        document.frontmatter, document.body, asset
    )
    retired_frontmatter["retired_into"] = ["sha256:" + "c" * 64]
    retired_content = (
        "---\n"
        + yaml.safe_dump(retired_frontmatter, sort_keys=False).rstrip()
        + "\n---\n"
        + document.body
    )
    target = store / f"{retired_frontmatter['content_hash'].removeprefix('sha256:')}.md"
    target.write_text(retired_content)
    manifest = staging / "manifest.json"
    manifest.write_text(json.dumps({"fetched_at": "2026-09-22T09:00:00Z"}))

    with pytest.raises(Record3Error, match="retired"):
        finalise_handler_record(intermediate, asset, manifest, output)

    assert intermediate.is_file()
    assert target.read_text() == retired_content


def test_legacy_migration_moves_all_old_authority_and_maps_exact_identity(tmp_path):
    output = tmp_path / "ingests"
    store = output / "store"
    records = tmp_path / "records"
    by_name = output / "by-name"
    for directory in (store, records, by_name):
        directory.mkdir(parents=True)
    data = b"archived HTML bytes"
    asset_hash = _sha(data)
    old_hash = "sha256:" + "1" * 64
    held = records / f"{asset_hash.removeprefix('sha256:')}.html"
    held.write_bytes(data)
    old = store / f"{old_hash.removeprefix('sha256:')}.v2.md"
    legacy = _legacy(
        asset_hash,
        schema="anomalica/record/2",
        content_hash=old_hash,
        source_hash=True,
        source_type="web",
        pages=None,
    ).replace("file_format: html\n", "file_format: html\narchived_ext: html\n")
    snapshot_bytes = b"%PDF retained page render"
    snapshot_hash = _sha(snapshot_bytes)
    (records / f"{snapshot_hash.removeprefix('sha256:')}.pdf").write_bytes(
        snapshot_bytes
    )
    legacy = legacy.replace(
        "date_accessed:",
        "snapshots:\n"
        "- role: page_render\n"
        f"  hash: {snapshot_hash}\n"
        "  content_type: application/pdf\n"
        "date_accessed:",
    )
    old.write_text(legacy)
    review = store / f"{old_hash.removeprefix('sha256:')}.review.json"
    review.write_text('{"human": true}\n')
    old_media = output / "media" / old_hash.removeprefix("sha256:")
    old_media.mkdir(parents=True)
    (old_media / "figure.jpg").write_bytes(b"figure")
    alias = by_name / "legacy.md"
    alias.symlink_to(os.path.relpath(old, by_name))

    result = migrate_legacy_record(old, records, output_dir=output)

    expected = record_identity(
        [{"asset_hash": asset_hash, "selector": {"type": "whole"}}]
    )
    assert result.content_hash == expected
    assert result.record_path == store / f"{expected.removeprefix('sha256:')}.md"
    assert alias.resolve() == result.record_path
    assert not result.record_path.with_suffix(".review.json").exists()
    assert result.legacy_path.name == f"{old_hash}.md"
    assert result.legacy_path.read_text() == legacy
    moved_review = result.legacy_path.with_name(f"{old_hash}.review.json")
    assert moved_review.read_text() == '{"human": true}\n'
    assert not old_media.exists()
    assert (
        output / "media" / result.record_path.stem / "figure.jpg"
    ).read_bytes() == b"figure"
    migrated = read_record(result.record_path).frontmatter
    assert migrated["legacy_identities"] == [
        {"schema": "anomalica/record/2", "content_hash": old_hash}
    ]
    assert migrated["snapshots"][0]["role"] == "page_render"
    assert migrated["snapshots"][0]["asset"]["asset_hash"] == snapshot_hash
    assert migrated["snapshots"][0]["asset"]["derived_from"] == {
        "asset_hash": asset_hash,
        "transform": "chromium-page-render-v1",
    }
    assert migrated["snapshots"][0]["asset"]["pages"] == 1
    identity_map = yaml.safe_load(result.identity_map_path.read_text())
    assert identity_map == {
        "schema": "anomalica/record-identity-map/1",
        "entries": [
            {
                "old_schema": "anomalica/record/2",
                "old_content_hash": old_hash,
                "new_content_hash": expected,
            }
        ],
    }


def test_legacy_migration_validates_before_writing(tmp_path):
    output = tmp_path / "ingests"
    store = output / "store"
    records = tmp_path / "records"
    store.mkdir(parents=True)
    records.mkdir()
    data = b"held bytes"
    asset_hash = _sha(data)
    old_hash = "sha256:" + "2" * 64
    held = records / f"{asset_hash.removeprefix('sha256:')}.html"
    held.write_bytes(b"different bytes")
    old = store / f"{old_hash.removeprefix('sha256:')}.md"
    legacy = _legacy(
        asset_hash,
        content_hash=old_hash,
        source_hash=True,
        source_type="web",
        pages=None,
    ).replace("file_format: html\n", "file_format: html\narchived_ext: html\n")
    old.write_text(legacy)

    with pytest.raises(Record3Error, match="do not match"):
        migrate_legacy_record(old, records, output_dir=output)

    assert old.read_text() == legacy
    assert not (store / "_record_identity_map.yaml").exists()
    assert not (store / "legacy-identities").exists()


def test_paged_legacy_migration_requires_an_exact_v9_source_map(tmp_path):
    output = tmp_path / "ingests"
    store = output / "store"
    records = tmp_path / "records"
    store.mkdir(parents=True)
    records.mkdir()
    data = b"held PDF bytes"
    asset_hash = _sha(data)
    old_hash = asset_hash
    (records / f"{asset_hash.removeprefix('sha256:')}.pdf").write_bytes(data)
    old = store / f"{old_hash.removeprefix('sha256:')}.md"
    legacy = _legacy(asset_hash, content_hash=old_hash).replace(
        "file_format: pdf\n", "file_format: pdf\narchived_ext: pdf\n"
    )
    legacy = legacy.replace("file_page: 2", "file_page: 9")
    old.write_text(legacy)

    with pytest.raises(Record3Error, match="source map"):
        migrate_legacy_record(old, records, output_dir=output)

    assert old.read_text() == legacy
    assert not (store / "_record_identity_map.yaml").exists()
    assert not (store / "legacy-identities").exists()


def test_legacy_migration_does_not_revive_retired_history(tmp_path):
    output = tmp_path / "ingests"
    store = output / "store"
    retired = store / "v1"
    records = tmp_path / "records"
    retired.mkdir(parents=True)
    records.mkdir()
    data = b"retired held bytes"
    asset_hash = _sha(data)
    (records / f"{asset_hash.removeprefix('sha256:')}.html").write_bytes(data)
    old_hash = "sha256:" + "9" * 64
    old = retired / f"{old_hash.removeprefix('sha256:')}.md"
    old.write_text(
        _legacy(
            asset_hash,
            content_hash=old_hash,
            source_hash=True,
            source_type="web",
            pages=None,
        ).replace("file_format: html\n", "file_format: html\narchived_ext: html\n")
    )

    with pytest.raises(Record3Error, match="top-level live"):
        migrate_legacy_record(old, records, output_dir=output)

    assert old.is_file()
    assert not (store / "_record_identity_map.yaml").exists()


def test_legacy_migration_refuses_many_old_to_one_selection(tmp_path):
    output = tmp_path / "ingests"
    store = output / "store"
    records = tmp_path / "records"
    store.mkdir(parents=True)
    records.mkdir()
    data = b"same asset"
    asset_hash = _sha(data)
    (records / f"{asset_hash.removeprefix('sha256:')}.html").write_bytes(data)
    records_to_migrate = []
    for character in ("3", "4"):
        old_hash = "sha256:" + character * 64
        path = store / f"{old_hash.removeprefix('sha256:')}.md"
        path.write_text(
            _legacy(
                asset_hash,
                content_hash=old_hash,
                source_hash=True,
                source_type="web",
                pages=None,
            ).replace("file_format: html\n", "file_format: html\narchived_ext: html\n")
        )
        records_to_migrate.append(path)

    with pytest.raises(Record3Error, match="several live legacy"):
        migrate_legacy_record(records_to_migrate[0], records, output_dir=output)

    assert all(path.exists() for path in records_to_migrate)
