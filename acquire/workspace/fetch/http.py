"""Simple HTTP fetcher with browser-like headers."""

from __future__ import annotations

import requests

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
TIMEOUT = 30


def fetch(url: str) -> tuple[bytes, str | None] | None:
    """Fetch a URL via HTTP GET. Returns (content_bytes, content_type) or None."""
    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
        )
        response.raise_for_status()
        return (response.content, response.headers.get("Content-Type"))
    except requests.RequestException:
        return None
