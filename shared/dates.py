"""One shape for `date_published`, without inventing precision.

Records carried three shapes for the same field: a bare date (2026-07-11), a
datetime with a midnight placeholder (2026-07-11 00:00:00+00:00) and an ISO
string (2026-07-20T00:00:00.000Z). After YAML parsing those are a date, a
datetime and a str, so anything sorting or comparing the field met three types.

The correction is to the TYPE, never the PRECISION. A partial date is legal and
load-bearing: a source that evidences only a year gets `2026`, and padding that
to `2026-01-01` would state a day the source does not. So YYYY and YYYY-MM pass
through, and only a time component is removed.
"""

from __future__ import annotations

from datetime import date, datetime

_PRECISIONS = ((10, "%Y-%m-%d"), (7, "%Y-%m"), (4, "%Y"))


def normalise_published(value: object) -> str:
    """`value` as a bare ISO date string at the precision it actually carries.

    Returns "" for an empty value, so the caller applies its own fallback rather
    than this helper inventing one. An unrecognised shape is returned stripped but
    otherwise untouched - a normaliser must not discard a value it cannot read,
    and the validator is where a malformed date gets reported.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
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

    for length, fmt in _PRECISIONS:
        prefix = candidate[:length]
        try:
            parsed = datetime.strptime(prefix, fmt)
        except ValueError:
            continue
        # Re-render from the parse so an unpadded "2026-7-1" comes back canonical,
        # at the precision that was matched and no finer.
        return parsed.strftime(fmt)
    return candidate
