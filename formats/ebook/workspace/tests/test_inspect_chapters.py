"""Chapter-boundary inspection: the deterministic scoping and pre-filter, and the
finding parser (with an injected fake model call, so no network)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inspect_chapters import (  # noqa: E402
    find_boundaries,
    inspect_boundary,
    needs_inspection,
)

BODY = """Front matter before any chapter is skipped.

# Chapter 1

W
hile exploring the evidence, I saw things.

More of chapter one.

## A subsection

Ordinary text here.

# Chapter 2

The second chapter opens cleanly.
"""


def test_find_boundaries_keys_off_headings_and_skips_front_matter():
    bs = find_boundaries(BODY)
    headings = [b.heading for b in bs]
    assert headings == ["# Chapter 1", "## A subsection", "# Chapter 2"]
    # the drop-cap split sits inside the first boundary's region
    assert "W\nhile" in bs[0].region
    # a boundary stops at the next heading, not run into it
    assert "# Chapter 2" not in bs[0].region


def test_needs_inspection_flags_dropcap_and_bad_marker_only():
    assert needs_inspection("# C\n\nW\nhile exploring") is True  # unresolved split
    assert needs_inspection("# C\n\nA clean opening sentence.") is False
    # a marker word that is not a well-formed comment is suspicious
    assert needs_inspection("# C\n\nprinted_page 12 stray text") is True
    assert needs_inspection("# C\n\n<!-- printed_page: 12 -->\n\nClean.") is False


def test_inspect_boundary_parses_issues_from_the_model():
    def fake_call(preamble, document, task, schema):
        return '{"issues": [{"type": "dropcap_split", "detail": "W/hile", "corrected": "While"}]}'

    issues = inspect_boundary("W\nhile exploring", fake_call)
    assert issues == [
        {"type": "dropcap_split", "detail": "W/hile", "corrected": "While"}
    ]


def test_inspect_boundary_survives_unparseable_output():
    issues = inspect_boundary("x", lambda *a: "not json at all")
    assert issues and issues[0]["type"] == "other"
