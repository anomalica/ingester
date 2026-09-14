import subprocess
import sys

import pytest

from canary_guard import CanaryError, main, validate_candidate


HASH = "a" * 64


def _record(*, generation=1, copyright="public_domain", carryover=False, body="Old"):
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
source_type: audio
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


def test_reviewed_candidate_requires_carryover_and_preserves_protected_fields():
    parent = _record()
    candidate = _record(generation=2, carryover=True, body="New")
    validate_candidate(parent, candidate, content_hash=HASH, reviewed=True)

    with pytest.raises(CanaryError, match="rights"):
        validate_candidate(
            parent,
            _record(
                generation=2,
                copyright="publicly_accessible",
                carryover=True,
                body="New",
            ),
            content_hash=HASH,
            reviewed=True,
        )

    with pytest.raises(CanaryError, match="review_carryover"):
        validate_candidate(
            parent,
            _record(generation=2, body="New"),
            content_hash=HASH,
            reviewed=True,
        )


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
    candidate.write_text(_record(generation=2, carryover=True, body="New"))
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
            carryover=True,
            body="New",
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
