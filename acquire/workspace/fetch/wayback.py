"""Wayback Machine fetcher - retrieves archived snapshots of web pages.

Uses the /web/<year>/<url> redirect-resolution endpoint rather than the
/wayback/available availability API. The availability API has been
returning empty results for many URLs even when snapshots exist; the
redirect endpoint resolves to the closest snapshot reliably.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import requests

REDIRECT_BASE = "https://web.archive.org/web/{year}/{url}"
TIMEOUT = 30

# A real wayback snapshot URL contains a 14-digit timestamp segment, e.g.
# /web/20250102020933/https://... When wayback has no snapshot for a URL
# the final response is a 404 or a non-snapshot page.
_SNAPSHOT_TIMESTAMP_RE = re.compile(r"/web/\d{14}/")


def fetch(url: str) -> tuple[bytes, str | None, dict | None] | None:
    """Fetch the closest Wayback Machine snapshot.

    Wayback often blocks the very latest snapshot (publishers can request
    that recent captures be hidden) while older captures of the same URL
    remain accessible. We try the current year first and fall back through
    progressively older year buckets until one returns 200.

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

        return (
            resp.content,
            resp.headers.get("Content-Type"),
            {"fetched_url": resp.url},
        )
    return None
