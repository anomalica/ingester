import hashlib
import importlib.util
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/migrate-record3.py"
SPEC = importlib.util.spec_from_file_location("migrate_record3_command", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
COMMAND = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMMAND)


def _git(repo: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return process.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, Path, str, str]:
    ingests = tmp_path / "ingests"
    records = tmp_path / "records"
    store = ingests / "store"
    store.mkdir(parents=True)
    records.mkdir()
    asset = b"held migration bytes"
    asset_hash = hashlib.sha256(asset).hexdigest()
    old_hash = "1" * 64
    (records / f"{asset_hash}.html").write_bytes(asset)
    (store / f"{old_hash}.md").write_text(
        "---\n"
        "schema: anomalica/record/1\n"
        'title: "Legacy fixture"\n'
        "source_type: web\n"
        "file_format: html\n"
        "archived_ext: html\n"
        f"content_hash: sha256:{old_hash}\n"
        f"source_hash: sha256:{asset_hash}\n"
        'date_accessed: "2026-09-22T09:00:00Z"\n'
        "source_id: url:fixture\n"
        "copyright:\n"
        "  status: publicly_accessible\n"
        "---\n"
        "Legacy body.\n"
    )
    media = ingests / "media" / old_hash
    media.mkdir(parents=True)
    (media / "figure.jpg").write_bytes(b"figure")
    _git(ingests, "init", "-q")
    _git(ingests, "add", ".")
    _git(
        ingests,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-qm",
        "initial",
    )
    return ingests, records, old_hash, _git(ingests, "rev-parse", "HEAD")


def test_command_commits_migration_and_advances_only_expected_head(tmp_path):
    ingests, records, old_hash, expected_head = _repository(tmp_path)

    commit = COMMAND.migrate(ingests, records, f"store/{old_hash}.md", expected_head)

    assert commit == _git(ingests, "rev-parse", "HEAD")
    assert _git(ingests, "rev-parse", "HEAD^") == expected_head
    assert _git(ingests, "status", "--porcelain", "--untracked-files=all") == ""
    legacy = ingests / "store/legacy-identities/record-1" / f"sha256:{old_hash}.md"
    assert legacy.is_file()
    assert (ingests / "store/_record_identity_map.yaml").is_file()
    live = [
        path
        for path in (ingests / "store").glob("*.md")
        if path.name != f"{old_hash}.md"
    ]
    assert len(live) == 1
    assert "schema: anomalica/record/3" in live[0].read_text()
    assert not (ingests / "media" / old_hash).exists()
    assert (ingests / "media" / live[0].stem / "figure.jpg").read_bytes() == b"figure"


def test_command_rejects_stale_head_without_writing(tmp_path):
    ingests, records, old_hash, _ = _repository(tmp_path)
    before = _git(ingests, "rev-parse", "HEAD")

    with pytest.raises(COMMAND.MigrationCommandError, match="HEAD changed"):
        COMMAND.migrate(ingests, records, f"store/{old_hash}.md", "0" * 40)

    assert _git(ingests, "rev-parse", "HEAD") == before
    assert (ingests / f"store/{old_hash}.md").is_file()
    assert not (ingests / "store/_record_identity_map.yaml").exists()
