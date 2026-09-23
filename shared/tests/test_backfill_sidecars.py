import hashlib
import importlib.util
from pathlib import Path

import yaml

from anomalica_common.identity import record_identity


MODULE_PATH = Path(__file__).resolve().parents[2] / "backfill_sidecars.py"
SPEC = importlib.util.spec_from_file_location("backfill_sidecars", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
BACKFILL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BACKFILL)


def test_record3_sidecar_resolves_the_asset_not_record_identity(tmp_path):
    ingests = tmp_path / "ingests"
    store = ingests / "store"
    records = tmp_path / "records"
    store.mkdir(parents=True)
    records.mkdir()
    source = b"restricted Asset bytes"
    asset_hash = "sha256:" + hashlib.sha256(source).hexdigest()
    content_hash = record_identity(
        [{"asset_hash": asset_hash, "selector": {"type": "whole"}}]
    )
    (records / f"{asset_hash.removeprefix('sha256:')}.pdf").write_bytes(source)
    frontmatter = {
        "schema": "anomalica/record/3",
        "content_hash": content_hash,
        "title": "Fixture",
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
    record_path = store / f"{content_hash.removeprefix('sha256:')}.md"
    record_path.write_text(
        "---\n"
        + yaml.safe_dump(frontmatter, sort_keys=False).rstrip()
        + "\n---\nFixture body.\n"
    )

    assert BACKFILL.backfill(ingests, records, force=False) == 0

    sidecar = yaml.safe_load(record_path.with_suffix(".verification.json").read_text())
    assert sidecar["sha256"] == asset_hash.removeprefix("sha256:")
    assert sidecar["size_bytes"] == len(source)
    assert sidecar["page_count"] == 1
