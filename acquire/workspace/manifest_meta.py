"""Map a yt-dlp info dict to the manifest metadata fields a record carries.

Single source of truth for the provenance fields (title, posted_by, posted_date,
duration, description, source_id) so the two paths that create an audio/video record
produce the SAME fields from the same source:

  - a fresh URL ingest (acquire fetches the URL, then maps its info json);
  - a local-file reprocess (`./ingest --source-url`, re-transcribing an archived
    .opus whose original record was deleted) which re-fetches the info json.

Before this was shared, the reprocess path stamped only source_url/source_id and
the record came out titled with the raw URL, no channel, dated the reprocess day.

NOTE (2026-08-19): acquire.py duplicates this mapping inline rather than importing
it, so this module currently has no production caller. Both were corrected together;
wiring acquire.py to call this - and deleting the duplicate - is the outstanding
tidy-up.
"""

from __future__ import annotations


def video_metadata_fields(info: dict) -> dict:
    """The manifest metadata fields derived from a yt-dlp info dict.

    Keys are OMITTED when their source is absent or malformed - never written as
    null - so a caller can `manifest.update(...)` without clobbering good values
    with empties. `upload_date` (yt-dlp's YYYYMMDD) becomes an ISO `posted_date`;
    `channel` becomes `posted_by`; `{extractor}:{id}` becomes a stable `source_id`.

    Both describe the COPY the fetcher saw, never the work. `publisher` and
    `date_published` are deliberately NOT emitted: a channel is the publisher only
    when it also produced the work, and yt-dlp cannot tell the difference, so they
    stay absent until someone identifies the work.
    """
    out: dict = {}
    if not isinstance(info, dict):
        return out
    title = info.get("title")
    if title:
        out["title"] = title
    ud = str(info.get("upload_date") or "")
    if len(ud) == 8 and ud.isdigit():
        out["posted_date"] = f"{ud[:4]}-{ud[4:6]}-{ud[6:8]}"
    dur = info.get("duration")
    if isinstance(dur, (int, float)) and dur > 0:
        out["duration"] = dur
    channel = info.get("channel")
    if channel:
        out["posted_by"] = channel
    description = info.get("description")
    if description:
        out["description"] = description
    extractor, vid = info.get("extractor"), info.get("id")
    if extractor and vid:
        out["source_id"] = f"{extractor}:{vid}"
    return out
