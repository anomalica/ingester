import hashlib
import os
import subprocess
import sys

import pytest

from recover_audio import main, prepare, recover
from record3 import Record3Error, read_record


def _setup(tmp_path):
    ingests = tmp_path / "ingests"
    store, aliases, archive = (
        ingests / "store",
        ingests / "by-name",
        tmp_path / "records",
    )
    for directory in (store, aliases, archive):
        directory.mkdir(parents=True)
    old_hash = "a" * 64
    old = store / f"{old_hash}.v2.md"
    old.write_text(
        "---\n"
        "schema: anomalica/record/2\n"
        f"content_hash: sha256:{old_hash}\n"
        "title: Example interview\nsource_type: video\nfile_format: opus\n"
        "source_url: https://www.youtube.com/watch?v=abcdefghijk\n"
        "source_id: youtube:abcdefghijk\n"
        'date_accessed: "2026-07-24T08:00:00Z"\n'
        'date_extracted: "2026-07-24T09:00:00Z"\n'
        "copyright:\n  status: publicly_accessible\n"
        "processing:\n  handler: audio\n  pipeline_version: 1\n"
        "  source:\n    audio:\n"
        f"    - sha256: {old_hash}\n      size_bytes: {len(b'reacquired opus')}\n"
        "---\n\n<!-- speaker: Speaker 1 -->\n{{t:1.25}}Hello {{t:1.63}}world.\n"
    )
    housekeeping = store / f"{old_hash}.housekeeping.json"
    housekeeping.write_text('{"pending": true}\n')
    link = aliases / "example.v2.md"
    link.symlink_to(os.path.relpath(old, aliases))
    audio = archive / f"{hashlib.sha256(b'reacquired opus').hexdigest()}.opus"
    audio.write_bytes(b"reacquired opus")
    return ingests, old, audio, link, housekeeping


def test_recovery_changes_asset_identity_and_preserves_old_body_and_sidecars(tmp_path):
    ingests, old, audio, link, housekeeping = _setup(tmp_path)
    old_body = read_record(old).body
    replacement = recover(old, audio, ingests)
    current = read_record(replacement)
    retired = read_record(ingests / "store/v1" / old.name)

    assert current.body == old_body == retired.body
    assert current.frontmatter["supersedes"] == old.stem.removesuffix(".v2")
    assert retired.frontmatter["superseded_by"] == replacement.stem
    assert current.frontmatter["assets"][0]["asset_hash"] == f"sha256:{audio.stem}"
    assert (
        current.frontmatter["assets"][0]["acquisition"]["acquired_at"]
        != "2026-07-24T08:00:00Z"
    )
    assert (
        current.frontmatter["processing"]["transcript_reused_from_asset"]
        == retired.frontmatter["content_hash"]
    )
    assert (
        current.frontmatter["processing"]["source"]["audio"][0]["sha256"] == audio.stem
    )
    assert not old.exists()
    assert (
        ingests / "store/v1" / housekeeping.name
    ).read_text() == '{"pending": true}\n'
    assert link.resolve() == replacement


def test_recovery_refuses_to_discard_human_review(tmp_path):
    ingests, old, audio, link, _ = _setup(tmp_path)
    (ingests / "store" / f"{old.stem.removesuffix('.v2')}.review.json").write_text("{}")
    with pytest.raises(Record3Error, match="human review"):
        prepare(old, audio, ingests)
    assert old.is_file()
    assert link.resolve() == old


def test_command_commits_only_the_replacement_paths(tmp_path, monkeypatch):
    ingests, old, audio, link, _ = _setup(tmp_path)
    subprocess.run(["git", "init", "-q", str(ingests)], check=True)
    subprocess.run(
        ["git", "-C", str(ingests), "add", "--", "store", "by-name"], check=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(ingests),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    for name, value in (
        ("GIT_AUTHOR_NAME", "Fixture"),
        ("GIT_AUTHOR_EMAIL", "fixture@example.test"),
        ("GIT_COMMITTER_NAME", "Fixture"),
        ("GIT_COMMITTER_EMAIL", "fixture@example.test"),
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "recover_audio.py",
            "--ingests-dir",
            str(ingests),
            "--records-dir",
            str(audio.parent),
            "--pair",
            f"{old.name.removesuffix('.v2.md')}:{audio.stem}",
        ],
    )
    assert main() == 0
    assert not old.exists()
    assert (
        read_record(link.resolve()).frontmatter["assets"][0]["asset_hash"]
        == f"sha256:{audio.stem}"
    )
    assert (
        subprocess.run(
            ["git", "-C", str(ingests), "status", "--porcelain"],
            capture_output=True,
            check=True,
        ).stdout
        == b""
    )
