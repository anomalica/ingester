import hashlib

import yaml

from anomalica_common.identity import record_identity
from verification import needs_sidecar, reconcile_record3_sidecar


def _record(asset_hash: str, status: str) -> str:
    labelled = f"sha256:{asset_hash}"
    content_hash = record_identity(
        [{"asset_hash": labelled, "selector": {"type": "whole"}}]
    )
    frontmatter = {
        "schema": "anomalica/record/3",
        "content_hash": content_hash,
        "title": "Fixture",
        "source_types": ["pdf"],
        "assets": [
            {
                "asset_hash": labelled,
                "file_format": "pdf",
                "archived_ext": "pdf",
                "source_type": "pdf",
                "pages": 1,
                "acquisition": {"acquired_at": "2026-09-22T09:00:00Z"},
                "copyright": {"status": status},
            }
        ],
        "selection": [{"asset_hash": labelled, "selector": {"type": "whole"}}],
        "page_map": [
            {
                "record_page": 1,
                "asset_hash": labelled,
                "asset_file_page": 1,
            }
        ],
    }
    return (
        "---\n"
        + yaml.safe_dump(frontmatter, sort_keys=False)
        + "---\n<!-- file_page: 1 -->\n"
        + "Enough source prose for a deterministic gate. " * 40
    )


def test_reconcile_uses_final_asset_rights_to_create_and_remove_gate(tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF exact source")
    asset_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    content_hash = record_identity(
        [
            {
                "asset_hash": f"sha256:{asset_hash}",
                "selector": {"type": "whole"},
            }
        ]
    ).removeprefix("sha256:")
    record = tmp_path / f"{content_hash}.md"
    record.write_text(_record(asset_hash, "restricted"))

    sidecar = reconcile_record3_sidecar(record, source)

    assert sidecar is not None
    assert sidecar.is_file()
    assert sidecar.name == f"{content_hash}.verification.json"

    record.write_text(_record(asset_hash, "public_domain"))
    assert reconcile_record3_sidecar(record, source) is None
    assert not sidecar.exists()


def test_snapshot_asset_rights_also_fail_closed():
    primary_hash = "sha256:" + "a" * 64
    frontmatter = {
        "schema": "anomalica/record/3",
        "assets": [{"copyright": {"status": "public_domain"}}],
        "snapshots": [
            {
                "role": "page_render",
                "asset": {
                    "asset_hash": "sha256:" + "b" * 64,
                    "copyright": {"status": "restricted"},
                    "derived_from": {"asset_hash": primary_hash},
                },
            }
        ],
    }
    content = "---\n" + yaml.safe_dump(frontmatter) + "---\nBody.\n"

    assert needs_sidecar(content)
