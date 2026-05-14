"""Simple HTTP fetcher with browser-like headers.

Skips HTML responses by design - those need to go through the patchright
fetcher so we capture a post-JS-render DOM and produce the PDF snapshot
that the workbench expects. HTTP stays fast for direct file downloads
(PDF, audio, ebook) where there is nothing to render.
"""

from __future__ import annotations

import requests

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
TIMEOUT = 30


def _is_html(content_type: str | None) -> bool:
    if not content_type:
        return False
    return "text/html" in content_type or "application/xhtml" in content_type


def fetch(url: str) -> tuple[bytes, str | None] | None:
    """Fetch a URL via HTTP GET. Returns (content_bytes, content_type) or None.

    Returns None for text/html responses so the patchright fetcher gets to
    handle them. Returns the response for any other content type.
    """
    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
        )
        response.raise_for_status()
        content_type = response.headers.get("Content-Type")
        if _is_html(content_type):
            return None
        return (response.content, content_type)
    except requests.RequestException:
        return None
