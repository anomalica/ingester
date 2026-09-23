import json
from pathlib import Path

import pytest
import yaml

from anomalica_common.identity import record_identity
from record_metadata import MetadataError, load_metadata, merge_manifest, merge_record


def _metadata(path: Path, **values) -> Path:
    path.write_text(
        json.dumps({"schema": "anomalica/record-metadata/1", "metadata": values})
    )
    return path


def _record(path: Path, **values) -> Path:
    frontmatter = {
        "schema": "anomalica/record/1",
        "title": "Handler title",
        "source_type": "pdf",
        "file_format": "pdf",
        "content_hash": "sha256:" + "a" * 64,
        **values,
    }
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\n"
        + yaml.safe_dump(frontmatter, sort_keys=False).strip()
        + "\n---\nBody text.\n"
    )
    return path


def test_load_metadata_rejects_unknown_and_nested_fields(tmp_path):
    unknown = _metadata(tmp_path / "unknown.json", title="Title", pairings=[])
    with pytest.raises(MetadataError, match="unsupported fields: pairings"):
        load_metadata(unknown)

    nested = _metadata(
        tmp_path / "nested.json",
        title="Title",
        copyright={"status": "public_domain", "evidence": "unchecked"},
    )
    with pytest.raises(MetadataError, match="must contain only status"):
        load_metadata(nested)


def test_merge_manifest_applies_identity_before_handler_dispatch(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "asset": "asset.mp4",
                "detected_type": "video/mp4",
                "source": "/tmp/audio-in-video-container.mp4",
            }
        )
    )
    metadata = _metadata(
        tmp_path / "metadata.json",
        title="Presentation by Captain Edward J. Ruppelt, 1952",
        source_type="audio",
        source_url="https://www.dvidshub.net/video/1023407/",
        source_id="DOW-UAP-PR160",
        description="Official catalogue blurb.\n\nSecond paragraph.",
        copyright={"status": "public_domain"},
    )

    merge_manifest(manifest, metadata)

    supplied = json.loads(manifest.read_text())
    assert supplied["original_type"] == "audio"
    assert supplied["source_id"] == "DOW-UAP-PR160"
    assert supplied["description"].endswith("Second paragraph.")
    assert supplied["copyright_status"] == "public_domain"


def test_merge_record_preserves_handler_fields_and_reconciles_alias(tmp_path):
    store = tmp_path / "store"
    by_name = tmp_path / "by-name"
    record = _record(
        store / ("a" * 64 + ".md"),
        pages=12,
        source_url="https://www.war.gov/report.pdf",
        source_id="DOW-UAP-D102",
        processing={"handler": "pdf"},
    )
    by_name.mkdir()
    old_alias = by_name / "undated-pdf-handler-title.md"
    old_alias.symlink_to(Path("../store") / record.name)
    metadata = _metadata(
        tmp_path / "metadata.json",
        title="Official title",
        source_type="pdf",
        source_url="https://www.war.gov/report.pdf",
        source_id="DOW-UAP-D102",
        publisher="Department of War",
        date_published="2026-09-18",
        description="Verbatim blurb.\n\nNo truncation.",
        copyright={"status": "public_domain"},
    )

    merge_record(record, metadata, by_name)

    frontmatter = yaml.safe_load(record.read_text().split("---", 2)[1])
    assert frontmatter["title"] == "Official title"
    assert frontmatter["description"] == "Verbatim blurb.\n\nNo truncation."
    assert frontmatter["pages"] == 12
    assert frontmatter["processing"] == {"handler": "pdf"}
    assert not old_alias.exists()
    alias = by_name / "2026-09-18-pdf-official-title.md"
    assert alias.resolve() == record.resolve()


def test_merge_record_fails_closed_on_identity_conflict(tmp_path):
    record = _record(
        tmp_path / "store" / ("a" * 64 + ".md"),
        source_id="DOW-UAP-DIFFERENT",
    )
    metadata = _metadata(
        tmp_path / "metadata.json",
        source_id="DOW-UAP-D102",
    )

    with pytest.raises(MetadataError, match="source_id conflicts"):
        merge_record(record, metadata, tmp_path / "by-name")

    assert "DOW-UAP-DIFFERENT" in record.read_text()


def test_merge_record3_places_metadata_in_authoritative_nested_blocks(tmp_path):
    asset_hash = "sha256:" + "b" * 64
    content_hash = record_identity(
        [{"asset_hash": asset_hash, "selector": {"type": "whole"}}]
    )
    frontmatter = {
        "schema": "anomalica/record/3",
        "content_hash": content_hash,
        "title": "Handler title",
        "source_types": ["pdf"],
        "source_type": "pdf",
        "file_format": "pdf",
        "assets": [
            {
                "asset_hash": asset_hash,
                "file_format": "pdf",
                "archived_ext": "pdf",
                "source_type": "pdf",
                "pages": 1,
                "acquisition": {"acquired_at": "2026-09-22T09:00:00Z"},
                "copyright": {"status": "restricted", "detail": "preserved"},
            }
        ],
        "selection": [{"asset_hash": asset_hash, "selector": {"type": "whole"}}],
        "page_map": [
            {
                "record_page": 1,
                "asset_hash": asset_hash,
                "asset_file_page": 1,
            }
        ],
        "snapshots": [
            {
                "role": "page_render",
                "asset": {
                    "asset_hash": "sha256:" + "c" * 64,
                    "file_format": "pdf",
                    "archived_ext": "pdf",
                    "source_type": "pdf",
                    "pages": 1,
                    "acquisition": {"acquired_at": "2026-09-22T09:00:00Z"},
                    "copyright": {"status": "restricted", "detail": "snapshot"},
                    "derived_from": {
                        "asset_hash": asset_hash,
                        "transform": "chromium-page-render-v1",
                    },
                },
            }
        ],
    }
    store = tmp_path / "store"
    store.mkdir()
    record = store / f"{content_hash.removeprefix('sha256:')}.md"
    record.write_text(
        "---\n"
        + yaml.safe_dump(frontmatter, sort_keys=False).strip()
        + "\n---\n<!-- file_page: 1 -->\nBody.\n"
    )
    metadata = _metadata(
        tmp_path / "metadata.json",
        title="Official title",
        source_type="pdf",
        source_url="https://example.test/report.pdf",
        source_id="DOW-UAP-D102",
        publisher="Department of War",
        date_published="2026-09-18",
        description="Verbatim description.",
        copyright={"status": "public_domain"},
    )

    merge_record(record, metadata, tmp_path / "by-name")

    merged = yaml.safe_load(record.read_text().split("---", 2)[1])
    assert merged["provenance"] == {
        "source_url": "https://example.test/report.pdf",
        "identifiers": {"source_id": "DOW-UAP-D102"},
        "publisher": "Department of War",
        "published_date": "2026-09-18",
        "description": "Verbatim description.",
    }
    assert merged["assets"][0]["copyright"] == {
        "status": "public_domain",
        "detail": "preserved",
    }
    assert merged["snapshots"][0]["asset"]["copyright"] == {
        "status": "public_domain",
        "detail": "snapshot",
    }
    assert "copyright" not in merged


def test_merge_record3_keeps_known_copy_id_on_asset(tmp_path):
    asset_hash = "sha256:" + "b" * 64
    content_hash = record_identity(
        [{"asset_hash": asset_hash, "selector": {"type": "whole"}}]
    )
    frontmatter = {
        "schema": "anomalica/record/3",
        "content_hash": content_hash,
        "title": "Fixture",
        "source_types": ["audio"],
        "source_type": "audio",
        "file_format": "opus",
        "assets": [
            {
                "asset_hash": asset_hash,
                "file_format": "opus",
                "archived_ext": "opus",
                "source_type": "audio",
                "acquisition": {
                    "acquired_at": "2026-09-22T09:00:00Z",
                    "copy_identifiers": {"source_id": "youtube:fixture"},
                },
                "copyright": {"status": "publicly_accessible"},
            }
        ],
        "selection": [{"asset_hash": asset_hash, "selector": {"type": "whole"}}],
    }
    store = tmp_path / "store"
    store.mkdir()
    record = store / f"{content_hash.removeprefix('sha256:')}.md"
    record.write_text(
        "---\n"
        + yaml.safe_dump(frontmatter, sort_keys=False).strip()
        + "\n---\nBody.\n"
    )
    metadata = _metadata(tmp_path / "metadata.json", source_id="youtube:fixture")

    merge_record(record, metadata, tmp_path / "by-name")

    merged = yaml.safe_load(record.read_text().split("---", 2)[1])
    assert merged["assets"][0]["acquisition"]["copy_identifiers"] == {
        "source_id": "youtube:fixture"
    }
    assert "provenance" not in merged
