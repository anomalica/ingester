"""A truncated Wayback capture must not become a complete-looking record."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fetch.wayback import _truncated_capture

HEADER = "x-archive-orig-x-crawler-content-length"


class _Resp:
    def __init__(self, headers: dict, content: bytes):
        self.headers = headers
        self.content = content


def test_detects_the_crawler_cap():
    """The observed case: a 2,617,753-byte PDF stored as exactly one mebibyte.

    The response is 200 with Content-Type application/pdf and the file opens far
    enough to look real, so nothing downstream notices. The only evidence is the
    original length the capture itself reports.
    """
    resp = _Resp({HEADER: "2617753"}, b"x" * 1048576)
    assert "truncated" in (_truncated_capture(resp) or "")


def test_a_whole_capture_passes():
    assert _truncated_capture(_Resp({HEADER: "2617753"}, b"x" * 2617753)) is None


def test_a_capture_without_the_header_is_not_judged():
    """Absence of the header means unknown, never truncated - refusing captures we
    cannot measure would reject most of the archive."""
    assert _truncated_capture(_Resp({}, b"short")) is None


def test_a_trivial_shortfall_is_tolerated():
    """Transfer encoding can shift a byte or two; only a real shortfall counts."""
    assert _truncated_capture(_Resp({HEADER: "1000"}, b"x" * 999)) is None
