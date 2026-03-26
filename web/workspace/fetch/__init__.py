"""Web page fetch layer.

Provides an ordered list of fetchers. Each fetcher has the same interface:
fetch(url: str) -> str | None (returns HTML or None).

The orchestrator iterates FETCHERS in order, trying extraction after each
successful fetch to determine whether the HTML contains usable content.
"""

from fetch import http, wayback

FETCHERS = [
    ("http", http.fetch),
    ("wayback", wayback.fetch),
]
