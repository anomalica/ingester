"""Asset acquisition fetch layer.

Each fetcher takes a URL and returns (content_bytes, content_type_header)
or None on failure. The content_type_header is the raw Content-Type value
from the HTTP response (may be None for browser-based fetchers).
"""

from fetch import http, patchright_fetch, wayback

FETCHERS = [
    ("http", http.fetch),
    ("wayback", wayback.fetch),
    ("patchright", patchright_fetch.fetch),
]
