"""Wayback Machine fetcher - retrieves archived snapshots of web pages.

Uses the /web/<year>/<url> redirect-resolution endpoint rather than the
/wayback/available availability API. The availability API has been
returning empty results for many URLs even when snapshots exist; the
redirect endpoint resolves to the closest snapshot reliably.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

REDIRECT_BASE = "https://web.archive.org/web/{year}/{url}"
# archive.org snapshots can be slow to reconstruct - measured up to ~90s for
# an older capture, so a 30s timeout dropped real content on the floor.
TIMEOUT = 90

# A wayback snapshot URL carries a 14-digit timestamp, optionally with a raw
# modifier (id_ = raw bytes, if_/im_ = iframe/image). When wayback has no
# snapshot the final response is a 404 or a non-snapshot page.
_SNAPSHOT_TIMESTAMP_RE = re.compile(r"/web/\d{14}(?:id_|if_|im_)?/")
_EMBEDDED_URL_RE = re.compile(r"/web/\d{14}(?:id_|if_|im_)?/(https?://.*)")


def _embedded_url(snapshot_url: str) -> str | None:
    """The original URL embedded in a wayback snapshot URL, or None."""
    m = _EMBEDDED_URL_RE.search(snapshot_url)
    return m.group(1) if m else None


def _diverged(requested: str, landed: str | None) -> bool:
    """True when `landed` is on a different path than `requested`.

    Used to spot a snapshot that followed an ARCHIVED redirect to a different
    original URL - a dead page captured mid-redirect (e.g. a removed article
    whose recent captures 3xx to `/news`). That capture is not the requested
    article, so it must be skipped. Descend/canonicalise redirects (same path
    or a sub-path) are not treated as divergence.
    """
    if not landed:
        return False
    req = urlparse(requested).path.strip("/")
    lan = urlparse(landed).path.strip("/")
    if not req:
        return False
    if lan == req:
        return False
    req_segs = req.split("/")
    lan_segs = lan.split("/")
    return lan_segs[: len(req_segs)] != req_segs


def _raw_url(snapshot_url: str) -> str:
    """The raw (id_) form of a snapshot URL - original archived bytes without
    the Wayback navigation chrome injected into the wrapped view."""
    if re.search(r"/web/\d{14}(?:id_|if_|im_)/", snapshot_url):
        return snapshot_url  # already carries a modifier
    return re.sub(r"(/web/\d{14})/", r"\1id_/", snapshot_url, count=1)


def _fetch_raw_or_wrapped(
    resp: requests.Response,
) -> tuple[bytes, str | None, dict] | None:
    """Prefer the raw (id_) capture of a resolved snapshot; fall back to the
    already-fetched wrapped response."""
    raw_url = _raw_url(resp.url)
    if raw_url != resp.url:
        try:
            raw = requests.get(raw_url, timeout=TIMEOUT, allow_redirects=True)
            if raw.status_code == 200 and raw.content:
                return (
                    raw.content,
                    raw.headers.get("Content-Type"),
                    {"fetched_url": raw.url},
                )
        except requests.RequestException:
            pass
    return (resp.content, resp.headers.get("Content-Type"), {"fetched_url": resp.url})


def fetch_snapshot(archive_url: str) -> tuple[bytes, str | None, dict] | None:
    """Fetch an EXPLICIT Wayback snapshot the operator pointed at - the exact
    timestamp they chose, in raw id_ mode. Honours that capture rather than
    re-resolving to a (possibly dead/redirected) recent snapshot. This is the
    reliable path for dead content: the operator finds a good snapshot and
    hands acquire its URL.

    Returns (content_bytes, content_type, metadata) or None.
    """
    for target in (_raw_url(archive_url), archive_url):
        try:
            resp = requests.get(target, timeout=TIMEOUT, allow_redirects=True)
        except requests.RequestException:
            continue
        if (
            resp.status_code == 200
            and resp.content
            and _SNAPSHOT_TIMESTAMP_RE.search(resp.url)
        ):
            return (
                resp.content,
                resp.headers.get("Content-Type"),
                {"fetched_url": resp.url},
            )
    return None


def fetch(url: str) -> tuple[bytes, str | None, dict | None] | None:
    """Fetch the closest Wayback Machine snapshot of a plain URL.

    Wayback often blocks the very latest snapshot (publishers can request
    that recent captures be hidden) while older captures of the same URL
    remain accessible. We try the current year first and fall back through
    progressively older year buckets until one returns 200. A snapshot that
    followed an archived redirect to a different URL (a dead page captured
    mid-redirect) is skipped - better to fail cleanly and let the operator
    point at a good snapshot (see fetch_snapshot) than to archive the wrong page.

    Returns (content_bytes, content_type, metadata) or None. The metadata
    dict contains 'fetched_url' with the full Wayback archive URL.
    """
    current_year = datetime.now(timezone.utc).year
    for year in (current_year, current_year - 1, current_year - 2, current_year - 3):
        target = REDIRECT_BASE.format(year=year, url=url)
        try:
            resp = requests.get(target, timeout=TIMEOUT, allow_redirects=True)
        except requests.RequestException:
            return None

        if resp.status_code != 200:
            continue
        if not _SNAPSHOT_TIMESTAMP_RE.search(resp.url):
            continue
        if _diverged(url, _embedded_url(resp.url)):
            continue

        return _fetch_raw_or_wrapped(resp)
    return None
