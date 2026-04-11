"""Wayback Machine fetcher - retrieves archived snapshots of web pages."""

from __future__ import annotations

import requests

AVAILABILITY_API = "https://archive.org/wayback/available"
TIMEOUT = 30


def fetch(url: str) -> tuple[bytes, str | None, dict | None] | None:
    """Fetch the closest Wayback Machine snapshot.

    Returns (content_bytes, content_type, metadata) or None. The metadata
    dict contains 'fetched_url' with the full Wayback archive URL.
    """
    try:
        resp = requests.get(AVAILABILITY_API, params={"url": url}, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        snapshots = data.get("archived_snapshots", {})
        closest = snapshots.get("closest")
        if not closest or closest.get("status") != "200":
            return None

        archive_url = closest["url"]
        page = requests.get(archive_url, timeout=TIMEOUT)
        page.raise_for_status()
        return (
            page.content,
            page.headers.get("Content-Type"),
            {"fetched_url": archive_url},
        )
    except requests.RequestException:
        return None
