import yaml

from audit_sources import archived_assets, archived_storage_key


def test_record3_archive_audit_includes_derivative_snapshot_assets():
    primary = "a" * 64
    snapshot = "b" * 64
    frontmatter = yaml.safe_dump(
        {
            "schema": "anomalica/record/3",
            "assets": [
                {
                    "asset_hash": f"sha256:{primary}",
                    "archived_ext": "html",
                    "source_type": "web",
                    "copyright": {"status": "publicly_accessible"},
                }
            ],
            "snapshots": [
                {
                    "role": "page_render",
                    "asset": {
                        "asset_hash": f"sha256:{snapshot}",
                        "archived_ext": "pdf",
                        "source_type": "web",
                        "copyright": {"status": "restricted"},
                    },
                }
            ],
        }
    )

    assert archived_assets(frontmatter) == [
        (primary, "html", "publicly_accessible", "web"),
        (snapshot, "pdf", "restricted", "web"),
    ]
    assert archived_storage_key(frontmatter, snapshot, "pdf") == (
        f"sources/{snapshot}.pdf"
    )


def test_legacy_remote_key_remains_record_identity_not_archive_hash():
    content_hash = "c" * 64
    asset_hash = "a" * 64
    frontmatter = yaml.safe_dump(
        {
            "schema": "anomalica/record/1",
            "content_hash": f"sha256:{content_hash}",
            "source_hash": f"sha256:{asset_hash}",
            "source_type": "ebook",
        }
    )

    assert archived_storage_key(frontmatter, asset_hash, "epub") == (
        f"sources/{content_hash}.epub"
    )
