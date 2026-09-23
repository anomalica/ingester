import yaml

from find_missing_provenance import find


def _write_record(store, name, frontmatter):
    (store / f"{name}.md").write_text(
        "---\n"
        + yaml.safe_dump(frontmatter, sort_keys=False).rstrip()
        + "\n---\nbody\n"
    )


def _record3(asset_hash):
    return {
        "schema": "anomalica/record/3",
        "content_hash": "sha256:" + "b" * 64,
        "title": "Unknown local document",
        "source_types": ["pdf"],
        "assets": [
            {
                "asset_hash": asset_hash,
                "file_format": "pdf",
                "archived_ext": "pdf",
                "source_type": "pdf",
                "pages": 1,
                "acquisition": {"acquired_at": "2026-09-23T08:00:00Z"},
                "copyright": {"status": "restricted"},
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
    }


def test_record3_missing_provenance_is_reported_from_asset_authority(tmp_path):
    asset_hash = "sha256:" + "a" * 64
    record = _record3(asset_hash)
    _write_record(tmp_path, "b" * 64, record)

    assert find(tmp_path) == [("b" * 16, "pdf", "Unknown local document")]


def test_record3_copy_identifier_clears_missing_provenance_queue(tmp_path):
    asset_hash = "sha256:" + "a" * 64
    record = _record3(asset_hash)
    record["assets"][0]["acquisition"]["copy_identifiers"] = {
        "source_id": "archiveorg:fixture"
    }
    _write_record(tmp_path, "b" * 64, record)

    assert find(tmp_path) == []
