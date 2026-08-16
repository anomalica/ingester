"""Site identity: the publisher's name, and the site chrome that clings to titles.

trafilatura hands back whatever the page declares as its site name, which is a
masthead on a good day and a tagline, a bare hostname or a title-cased domain
slug on a normal one - the corpus holds `Liberation Times | Reimagining Old
News` (23 records), `wikileaks.org`, `space.com` and `Nytimes`. The same chrome
rides along on titles: 22 articles were stored as `<Article> - Liberation Times
| Reimagining Old News`.

Names are load-bearing downstream: the assimilator keys nodes on them, so one
publisher under four spellings is four things.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

# Hosts whose declared site name is demonstrably wrong, with the name the
# publication actually uses. Deliberately only the cases observed in the corpus:
# an unknown host falls through to the cleaning rules below rather than having a
# name invented for it.
MASTHEADS = {
    "nytimes.com": "The New York Times",
    "wikileaks.org": "WikiLeaks",
    "space.com": "Space.com",
    "bibliotecapleyades.net": "Biblioteca Pleyades",
    "theblackvault.com": "The Black Vault",
    "liberationtimes.com": "Liberation Times",
}

# Ordered: the em-dash forms are the common title separator, the spaced hyphen
# the common tagline one. All require surrounding spaces, so a hyphenated name
# ("Sci-Fi Weekly") is never split.
SEPARATORS = (" | ", " — ", " – ", " - ")

_PUBLISHED_YEAR = re.compile(r"\s*\(Published\s+\d{4}\)\s*$")


def _host(url: str | None) -> str:
    if not url:
        return ""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def canonical_publisher(sitename: str | None, url: str | None = None) -> str:
    """The publisher's name for a page: a known masthead, else the declared site
    name with its tagline and stray whitespace removed.

    Returns "" when there is nothing to go on. An unrecognised site name is
    cleaned but never replaced - guessing a masthead from a domain produces
    exactly the `Nytimes` this exists to fix.
    """
    masthead = MASTHEADS.get(_host(url))
    if masthead:
        return masthead

    name = " ".join((sitename or "").split())
    if not name:
        return ""
    for sep in SEPARATORS:
        if sep in name:
            head = name.split(sep, 1)[0].strip()
            if head:
                return head
            break
    return name


def strip_site_suffix(
    title: str, sitename: str | None = None, url: str | None = None
) -> str:
    """A title with its trailing site chrome removed.

    Only strips a tail that IS the site's own name - the declared site name, the
    masthead, or the hostname - so a title whose real subtitle happens to follow a
    dash survives. A title that is nothing BUT chrome is left alone: emptying it
    would replace a bad title with no title.
    """
    cleaned = " ".join((title or "").split())
    if not cleaned:
        return cleaned

    cleaned = _PUBLISHED_YEAR.sub("", cleaned)

    host = _host(url)
    tails = [
        " ".join((sitename or "").split()),
        canonical_publisher(sitename, url),
        host,
        f"www.{host}" if host else "",
    ]
    for sep in SEPARATORS:
        for tail in tails:
            if tail and cleaned.endswith(sep + tail):
                remainder = cleaned[: -len(sep + tail)].strip()
                if remainder:
                    return remainder
    return cleaned
