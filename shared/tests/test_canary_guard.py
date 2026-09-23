import subprocess
import sys

import pytest
import yaml

from anomalica_common.identity import record_identity
from canary_guard import CanaryError, main, validate_candidate
from pipeline_version import current_version


HASH = "a" * 64


def _record(
    *,
    generation=1,
    copyright="public_domain",
    carryover=False,
    body="Old",
    source_type="audio",
):
    carry = (
        f"review_carryover:\n  at: 2026-09-14T00:00:00Z\n  from: {HASH}\n"
        "  had_text_edits: true\n"
        if carryover
        else ""
    )
    return f"""---
schema: anomalica/record/2
title: Reviewed audio
date_published: 1962-05-24
source_type: {source_type}
source_url: https://example.com/audio
content_hash: sha256:{HASH}
copyright:
  status: {copyright}
provenance:
  collection: archive
processing:
  handler: audio
  version: abc
  pipeline_version: {generation}
{carry}---
{body}
"""


def _record3(generation, body="Old"):
    asset_hash = "sha256:" + "b" * 64
    content_hash = record_identity(
        [{"asset_hash": asset_hash, "selector": {"type": "whole"}}]
    )
    frontmatter = {
        "schema": "anomalica/record/3",
        "content_hash": content_hash,
        "title": "Reviewed audio",
        "source_types": ["audio"],
        "source_type": "audio",
        "file_format": "opus",
        "assets": [
            {
                "asset_hash": asset_hash,
                "file_format": "opus",
                "archived_ext": "opus",
                "source_type": "audio",
                "acquisition": {"acquired_at": "2026-09-23T08:00:00Z"},
                "copyright": {"status": "public_domain"},
            }
        ],
        "selection": [{"asset_hash": asset_hash, "selector": {"type": "whole"}}],
        "processing": {
            "handler": "audio",
            "asset_pipeline_versions": [
                {
                    "asset_hash": asset_hash,
                    "source_type": "audio",
                    "pipeline_version": generation,
                }
            ],
        },
    }
    return (
        content_hash.removeprefix("sha256:"),
        "---\n"
        + yaml.safe_dump(frontmatter, sort_keys=False).rstrip()
        + f"\n---\n{body}\n",
    )


def test_reviewed_candidate_requires_carryover_and_preserves_protected_fields():
    parent = _record(source_type="web")
    candidate = _record(generation=7, carryover=True, body="New", source_type="web")
    validate_candidate(parent, candidate, content_hash=HASH, reviewed=True)

    with pytest.raises(CanaryError, match="rights"):
        validate_candidate(
            parent,
            _record(
                generation=7,
                copyright="publicly_accessible",
                carryover=True,
                body="New",
                source_type="web",
            ),
            content_hash=HASH,
            reviewed=True,
        )

    with pytest.raises(CanaryError, match="review_carryover"):
        validate_candidate(
            parent,
            _record(generation=7, body="New", source_type="web"),
            content_hash=HASH,
            reviewed=True,
        )


def test_reviewed_audio_candidate_must_preserve_the_exact_body():
    parent = _record(body="<!-- speaker: Person -->\n{{t:0.00}}A word")
    changed = _record(
        generation=2,
        carryover=True,
        body="<!-- speaker: Person -->\n{{t:0.10}}Another word",
    )
    with pytest.raises(CanaryError, match="audio/video body changed"):
        validate_candidate(parent, changed, content_hash=HASH, reviewed=True)

    unchanged = _record(generation=2, body="<!-- speaker: Person -->\n{{t:0.00}}A word")
    validate_candidate(parent, unchanged, content_hash=HASH, reviewed=True)


def test_record3_candidate_uses_asset_pipeline_generation():
    content_hash, parent = _record3(1)
    _, candidate = _record3(current_version("audio"))

    validate_candidate(parent, candidate, content_hash=content_hash, reviewed=False)


def test_isolated_guard_accepts_or_refuses_without_touching_live_parent(
    tmp_path, monkeypatch
):
    repo = tmp_path / "ingests"
    store = repo / "store"
    store.mkdir(parents=True)
    record = store / f"{HASH}.v2.md"
    parent = _record()
    record.write_text(parent)
    (store / f"{HASH}.review.json").write_text('{"reviews": []}\n')
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "store"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "parent",
        ],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    candidate = tmp_path / "candidate.md"
    candidate.write_text(_record(generation=2, body="Old"))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "canary_guard.py",
            "--ingests-dir",
            str(repo),
            "--parent",
            head,
            "--record-path",
            f"store/{record.name}",
            "--candidate-path",
            str(candidate),
        ],
    )
    assert main() == 0
    assert record.read_text() == parent

    candidate.write_text(
        _record(
            generation=2,
            copyright="publicly_accessible",
            body="Old",
        )
    )
    with pytest.raises(CanaryError, match="rights"):
        main()
    assert record.read_text() == parent
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == head
    )
