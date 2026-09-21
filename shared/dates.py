"""Canonical temporal values without inventing or discarding precision.

Records carried three shapes for the same field: a bare date (2026-07-11), a
datetime with a midnight placeholder (2026-07-11 00:00:00+00:00) and an ISO
string (2026-07-20T00:00:00.000Z). After YAML parsing those are a date, a
datetime and a str, so anything sorting or comparing the field met three types.

The correction is to the TYPE, never the PRECISION. A partial date is legal and
load-bearing: a source that evidences only a year gets `2026`, and padding that
to `2026-01-01` would state a day the source does not. An evidenced publication
instant keeps its time and offset; acquisition and extraction instants always
carry an offset.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

_PRECISIONS = (
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "%Y-%m-%d"),
    (re.compile(r"\d{4}-\d{2}"), "%Y-%m"),
    (re.compile(r"\d{4}"), "%Y"),
)
_FULL_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_RFC3339 = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
)


def is_rfc3339_instant(value: object) -> bool:
    """Whether ``value`` is an RFC 3339 instant with an explicit UTC offset."""
    if isinstance(value, datetime):
        return value.tzinfo is not None and value.utcoffset() is not None
    if not isinstance(value, str) or not _RFC3339.fullmatch(value):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.utcoffset() is not None


def is_utc_instant(value: object) -> bool:
    """Whether ``value`` is a canonical RFC 3339 instant ending in Z."""
    return isinstance(value, str) and value.endswith("Z") and is_rfc3339_instant(value)


def is_evidenced_date(value: object) -> bool:
    """Whether a publication value is a valid partial date or offset instant."""
    if isinstance(value, datetime):
        return is_rfc3339_instant(value)
    if isinstance(value, date):
        return True
    if not isinstance(value, str):
        return False
    if is_rfc3339_instant(value):
        return True
    for pattern, fmt in _PRECISIONS:
        if pattern.fullmatch(value):
            try:
                datetime.strptime(value, fmt)
            except ValueError:
                return False
            return True
    return False


def is_full_date(value: object) -> bool:
    """Whether ``value`` is a calendar date with day precision."""
    if isinstance(value, datetime):
        return False
    if isinstance(value, date):
        return True
    if not isinstance(value, str) or not _FULL_DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def utc_now_rfc3339() -> str:
    """The current UTC instant in canonical RFC 3339 Z form."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def temporal_scalar(value: object) -> str:
    """Quote a temporal value so YAML always loads it as a string."""
    text = str(value)
    return '"{}"'.format(text.replace("\\", "\\\\").replace('"', '\\"'))


def normalise_published(value: object) -> str:
    """``value`` as ISO text at the precision it actually carries.

    Returns "" for an empty value, so the caller applies its own fallback rather
    than this helper inventing one. An unrecognised shape is returned stripped but
    otherwise untouched - a normaliser must not discard a value it cannot read,
    and the validator is where a malformed date gets reported.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()

    candidate = str(value).strip()
    if not candidate:
        return ""

    # yt-dlp's YYYYMMDD, before the prefix match below can mistake it for a year.
    if len(candidate) == 8 and candidate.isdigit():
        try:
            return datetime.strptime(candidate, "%Y%m%d").date().isoformat()
        except ValueError:
            return candidate

    # Normalise a valid offset timestamp, including YAML's space-separated form.
    timestamp_candidate = candidate.replace(" ", "T", 1)
    if is_rfc3339_instant(timestamp_candidate):
        return timestamp_candidate

    for _pattern, fmt in _PRECISIONS:
        try:
            parsed = datetime.strptime(candidate, fmt)
        except ValueError:
            continue
        # Re-render from the parse so an unpadded "2026-7-1" comes back canonical,
        # at the precision that was matched and no finer.
        return parsed.strftime(fmt)
    return candidate


def published_scalar(value: object) -> str:
    """`normalise_published` rendered as the YAML scalar to write in frontmatter.

    Every temporal value is quoted so YAML gives every consumer one lexical string
    type. Precision remains encoded by the value itself.
    """
    text = normalise_published(value)
    if not text:
        return ""
    return temporal_scalar(text)


def date_alias(value: object) -> str | None:
    """Calendar portion suitable for a human filename, without changing metadata."""
    text = normalise_published(value)
    if not text:
        return None
    return text[:10] if _FULL_DATE.match(text) else text
