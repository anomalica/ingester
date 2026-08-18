"""Repair logic: record round-trip, the whitespace-only join guard, and that
structural findings are reported not applied. No network - the model call is faked."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "shared"))

from repair_book import (  # noqa: E402
    _apply_verified_join,
    repair_body,
    split_record,
)

RECORD = """---
schema: anomalica/record/1
title: "Test"
content_hash: sha256:abc
---

# Chapter 1

W
hile exploring the world.

I
was there too.

# Chapter 2

Clean.
"""


def test_split_record_round_trips():
    fm, body = split_record(RECORD)
    assert f"{fm}\n\n{body}" == RECORD
    assert fm.startswith("---") and fm.endswith("---")


def test_split_record_rejects_no_frontmatter():
    import pytest

    with pytest.raises(ValueError):
        split_record("no frontmatter here")


def test_apply_verified_join_takes_only_the_spacing_decision():
    # model says spaced (even with extra trailing text) -> a space is inserted
    assert (
        _apply_verified_join("I\nwas here", "I", "was", "I was afraid") == "I was here"
    )
    # model says joined -> no space
    assert (
        _apply_verified_join("I\nndridi was", "I", "ndridi", "Indridi") == "Indridi was"
    )
    # correction that fits neither form is refused, body unchanged
    assert _apply_verified_join("I\nwas", "I", "was", "something else") == "I\nwas"


def test_repair_applies_dropcaps_and_reports_structural():
    def fake_call(preamble, document, task, schema):
        # 'I'+'was' -> spaced; also report a byline-as-heading (structural)
        return (
            '{"issues": [{"type": "dropcap_split", "detail": "I/was", "corrected": "I was"},'
            ' {"type": "title_problem", "detail": "byline promoted to heading"}]}'
        )

    _, body = split_record(RECORD)
    new_body, res = repair_body(body, fake_call)
    assert res.dropcaps_fixed == 1  # 'W'+'hile' deterministic
    assert res.ai_splits_fixed == 1  # 'I'+'was' via the fake model
    assert "While exploring" in new_body and "I was there too" in new_body
    assert any(s["type"] == "title_problem" for s in res.structural)


def test_repair_skips_reviewed_records():
    _, body = split_record(RECORD)
    out, res = repair_body(body, lambda *a: '{"issues": []}', is_reviewed=True)
    assert out == body and res.changed is False and res.skipped_reason
