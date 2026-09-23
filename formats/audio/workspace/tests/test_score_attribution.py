from pathlib import Path

from score_attribution import record_path_for_asset


def test_record_path_resolves_record3_selection_identity(tmp_path: Path):
    asset_hash = "a" * 64
    record_hash = "b" * 64
    record = tmp_path / f"{record_hash}.md"
    record.write_text(
        "---\n"
        "schema: anomalica/record/3\n"
        f"content_hash: sha256:{record_hash}\n"
        "assets:\n"
        f"- asset_hash: sha256:{asset_hash}\n"
        "selection:\n"
        f"- asset_hash: sha256:{asset_hash}\n"
        "  selector:\n"
        "    type: whole\n"
        "---\nbody\n"
    )

    assert record_path_for_asset(tmp_path, asset_hash) == record


def test_record_path_preserves_legacy_record2_preference(tmp_path: Path):
    asset_hash = "c" * 64
    legacy = tmp_path / f"{asset_hash}.md"
    word_timed = tmp_path / f"{asset_hash}.v2.md"
    legacy.write_text("legacy")
    word_timed.write_text("word timed")

    assert record_path_for_asset(tmp_path, asset_hash) == word_timed
