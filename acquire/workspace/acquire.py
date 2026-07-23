#!/usr/bin/env python3
"""Asset acquisition - fetches a URL and writes the result to a staging directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from detect import detect
from fetch import FETCHERS
from fetch.ytdlp import is_video_platform


WAYBACK_PREFIX = "https://web.archive.org/web/"


def _ytdlp_creators(meta: dict) -> list[str]:
    """Human creators (host/presenter) from yt-dlp metadata, where distinct from
    the channel. yt-dlp exposes creator/artist only sometimes; often the only
    name is the channel/uploader, which belongs in `publisher`, not `creators`.
    Returns [] when there is no distinct creator, leaving the field for a
    reviewer to fill.
    """
    channel = (meta.get("channel") or meta.get("uploader") or "").strip().casefold()
    raw = (
        meta.get("creators")
        or meta.get("artists")
        or meta.get("creator")
        or meta.get("artist")
    )
    if not raw:
        return []
    values = raw if isinstance(raw, list) else [p.strip() for p in str(raw).split(",")]
    return [v for v in (s.strip() for s in values) if v and v.casefold() != channel]


def _url_source_id(url: str) -> str:
    """Generate a stable source_id for a URL by hashing it.

    Used when no platform-specific extractor (like yt-dlp) provides one.
    Format: 'url:{12-char hash}' for readability.
    """
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return f"url:{h[:12]}"


def _redirected_away(requested: str, final: str | None) -> bool:
    """True when a fetch LEFT the requested resource - redirected to the site
    root or an unrelated/shorter path. This is the signature of a dead content
    URL that 3xx-collapses to a homepage or a generic section (e.g. a removed
    article -> the publisher's `/news` front). The landing page is not the
    requested article, so a capture there must not be preferred over an
    archived copy that still holds the real content.

    Conservative: a redirect that keeps the requested path or DESCENDS into it
    (scheme/host/trailing-slash canonicalisation, an appended sub-path) is not
    flagged - only a divergence onto a different path is.
    """
    if not final:
        return False
    req = urlparse(requested).path.strip("/")
    fin = urlparse(final).path.strip("/")
    if not req:
        return False  # requested the homepage already - nothing lost
    if not fin:
        return True  # collapsed to the site root
    req_segs = req.split("/")
    fin_segs = fin.split("/")
    # final descends into (or equals) the requested path -> same resource area
    if fin_segs[: len(req_segs)] == req_segs:
        return False
    return True


def _parse_wayback_url(url: str) -> tuple[str, str] | None:
    """If url is a Wayback Machine URL, extract the original URL and archive URL.

    Returns (original_url, archive_url) or None if not a Wayback URL.
    Wayback format: https://web.archive.org/web/{timestamp}/{original_url}
    """
    if not url.startswith(WAYBACK_PREFIX):
        return None
    rest = url[len(WAYBACK_PREFIX) :]
    # Format is {timestamp}/{original_url} - timestamp is digits, then /
    slash_pos = rest.find("/")
    if slash_pos < 1:
        return None
    original_url = rest[slash_pos + 1 :]
    if not original_url.startswith("http"):
        return None
    return (original_url, url)


MIME_TO_EXT = {
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "application/pdf": ".pdf",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/opus": ".opus",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "audio/flac": ".flac",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "application/epub+zip": ".epub",
}

MIN_HTML_SIZE = 1024


def acquire(url: str, staging_dir: Path) -> int:
    """Fetch a URL and write the asset and manifest to staging_dir.

    If the URL is a Wayback Machine URL, extracts the original URL and
    records the Wayback URL as the fetched_url. Returns 0 on success, 1 on failure.
    """
    staging_dir.mkdir(parents=True, exist_ok=True)

    # If the input is a Wayback Machine URL, decompose it
    wayback_parsed = _parse_wayback_url(url)
    if wayback_parsed:
        original_url, archive_url = wayback_parsed
        url = original_url
        forced_fetched_url = archive_url
    else:
        forced_fetched_url = None

    # A video-platform URL (youtube etc.) is fetched by yt-dlp ONLY. If yt-dlp
    # fails, the ingest errors rather than falling through to a fetcher that
    # would scrape the page shell (an HTML record with no real content). A
    # missing record is recoverable; a garbage one silently pollutes the corpus.
    fetchers = FETCHERS
    if is_video_platform(url):
        fetchers = [(name, fn) for name, fn in FETCHERS if name == "ytdlp"]
    elif wayback_parsed:
        # The operator pointed at a SPECIFIC archived snapshot - fetch that exact
        # capture (honouring their chosen timestamp), first and by preference, and
        # do NOT re-fetch the live original: for a dead URL the live original
        # redirects to a generic page. A distinct method name keeps the
        # wayback+live override (which fires only for method "wayback") off. Falls
        # through to the normal fetchers if the explicit snapshot is unreachable.
        from fetch import wayback as _wb

        fetchers = [
            ("wayback-snapshot", lambda _u, _a=archive_url: _wb.fetch_snapshot(_a))
        ] + list(FETCHERS)

    for method_name, fetcher in fetchers:
        print(f"Trying {method_name}...", file=sys.stderr)
        result = fetcher(url)
        if result is None:
            print(f"  {method_name}: no response", file=sys.stderr)
            continue

        # Fetchers return (content, content_type) or (content, content_type, metadata)
        fetcher_metadata = None
        if len(result) == 3:
            content, content_type_header, fetcher_metadata = result
        else:
            content, content_type_header = result

        detected_type = detect(
            data=content,
            content_type_header=content_type_header,
        )

        if not detected_type:
            detected_type = "application/octet-stream"

        is_html = detected_type in ("text/html", "application/xhtml+xml")

        if is_html and len(content) < MIN_HTML_SIZE:
            print(
                f"  {method_name}: response too small ({len(content)} bytes), trying next",
                file=sys.stderr,
            )
            continue

        # A live browser capture (patchright) that 3xx-redirected off the
        # requested article - to the site root or a generic section - is not a
        # capture of that article. With no archived copy to fall back to on this
        # path, reject it and fail cleanly rather than ingest the landing page as
        # the article (a missing record is recoverable; a garbage one is not).
        # Only patchright reports final_url, so this never touches other fetchers.
        if is_html and _redirected_away(url, (fetcher_metadata or {}).get("final_url")):
            print(
                f"  {method_name}: capture redirected away to "
                f"{(fetcher_metadata or {}).get('final_url')} - not the requested "
                "page, trying next",
                file=sys.stderr,
            )
            continue

        ext = MIME_TO_EXT.get(detected_type, ".bin")
        asset_name = f"asset{ext}"
        (staging_dir / asset_name).write_bytes(content)
        asset_hash = hashlib.sha256(content).hexdigest()

        manifest = {
            "source": url,
            "asset": asset_name,
            "asset_hash": asset_hash,
            "detected_type": detected_type,
            "fetch_method": method_name,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

        # Wayback delivers the archived page body but no snapshot artefacts, and
        # its copy of a live site is often paywalled or thin. Fetch the LIVE
        # ORIGINAL with patchright - both to produce the snapshots and to serve
        # as the source of record when it succeeds, the way a real browser gets
        # the full article. Wayback stays the fallback for sites that block
        # headless browsers (the live fetch returns nothing and the archived
        # asset is kept). Only wayback reaches here for HTML: http returns None
        # for text/html so patchright wins live pages directly (with snapshots).
        snapshots_from_fetcher = (
            fetcher_metadata.get("snapshots") if fetcher_metadata else None
        )
        if is_html and not snapshots_from_fetcher and method_name == "wayback":
            print(f"  fetching live original via patchright: {url}", file=sys.stderr)
            from fetch.patchright_fetch import fetch as _patchright_fetch

            pr_result = _patchright_fetch(url)
            if pr_result and len(pr_result) == 3:
                pr_content, pr_ctype, pr_metadata = pr_result
                live_final_url = (pr_metadata or {}).get("final_url")
                if _redirected_away(url, live_final_url):
                    # The live original 3xx-redirected off the requested article
                    # (to the site root or a generic section). The article is
                    # dead; keep the archived copy the operator pointed at rather
                    # than preferring the generic landing page. Without this, a
                    # dead-article-redirects-to-a-section silently overrides the
                    # real archived content.
                    print(
                        f"  live original redirected away to {live_final_url}"
                        " - keeping archived copy",
                        file=sys.stderr,
                    )
                else:
                    if pr_metadata and pr_metadata.get("snapshots"):
                        snapshots_from_fetcher = pr_metadata["snapshots"]
                    # Prefer the live capture as the asset when it returned usable
                    # HTML - it carries the full page, not the archived paywalled
                    # copy. Falls back to the archived asset when the live fetch is
                    # blocked or empty.
                    pr_is_html = (pr_ctype or "").startswith("text/html")
                    if pr_content and pr_is_html and len(pr_content) >= MIN_HTML_SIZE:
                        content = pr_content
                        detected_type = pr_ctype or detected_type
                        asset_hash = hashlib.sha256(content).hexdigest()
                        (staging_dir / asset_name).write_bytes(content)
                        manifest["asset_hash"] = asset_hash
                        manifest["detected_type"] = detected_type
                        manifest["fetch_method"] = f"{method_name}+live"
                        if fetcher_metadata is not None:
                            fetcher_metadata["fetched_url"] = url
                        print(
                            f"  using live original as asset ({len(content)} bytes)",
                            file=sys.stderr,
                        )

        # Sibling snapshots (e.g. PDF render of an HTML page) - written to
        # staging alongside the main asset and recorded in the manifest so
        # the host script can archive them under sources/{hash}.{ext}.
        if snapshots_from_fetcher:
            snapshot_entries = []
            for idx, snap in enumerate(snapshots_from_fetcher):
                snap_ext = snap["extension"]
                snap_name = f"snapshot_{idx}.{snap_ext}"
                snap_bytes = snap["bytes"]
                (staging_dir / snap_name).write_bytes(snap_bytes)
                snapshot_entries.append(
                    {
                        "path": snap_name,
                        "extension": snap_ext,
                        "content_type": snap["content_type"],
                        "role": snap.get("role", "snapshot"),
                        "hash": hashlib.sha256(snap_bytes).hexdigest(),
                    }
                )
            manifest["snapshots"] = snapshot_entries

        # Include metadata from fetcher if available (e.g. yt-dlp title, date)
        if fetcher_metadata:
            if fetcher_metadata.get("title"):
                manifest["title"] = fetcher_metadata["title"]
            upload_date = fetcher_metadata.get("upload_date")
            if upload_date and len(upload_date) == 8:
                manifest["date"] = (
                    f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
                )
            if fetcher_metadata.get("duration"):
                manifest["duration"] = fetcher_metadata["duration"]
            if fetcher_metadata.get("media_type"):
                manifest["original_type"] = fetcher_metadata["media_type"]
            if fetcher_metadata.get("source_id"):
                manifest["source_id"] = fetcher_metadata["source_id"]
            if fetcher_metadata.get("channel"):
                manifest["publisher"] = fetcher_metadata["channel"]
            creators = _ytdlp_creators(fetcher_metadata)
            if creators:
                manifest["creators"] = creators
            if fetcher_metadata.get("description"):
                manifest["description"] = fetcher_metadata["description"]

        # Fall back to URL-derived source_id if fetcher didn't provide one
        if "source_id" not in manifest:
            manifest["source_id"] = _url_source_id(url)

        # Record the actual URL we fetched from (may differ from source URL)
        if forced_fetched_url:
            manifest["fetched_url"] = forced_fetched_url
        elif fetcher_metadata and fetcher_metadata.get("fetched_url"):
            manifest["fetched_url"] = fetcher_metadata["fetched_url"]
        else:
            manifest["fetched_url"] = url

        (staging_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

        print(f"  {method_name}: success ({detected_type})", file=sys.stderr)
        return 0

    error = (
        "yt-dlp could not fetch this video-platform URL (not falling back to a "
        "page-shell scrape)"
        if is_video_platform(url)
        else "All fetch methods exhausted"
    )
    manifest = {
        "source": url,
        "asset": None,
        "detected_type": None,
        "fetch_method": None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "error": error,
    }
    (staging_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(error, file=sys.stderr)
    return 1


def main():
    parser = argparse.ArgumentParser(description="Fetch a URL and stage the result.")
    parser.add_argument("url", help="URL to fetch")
    parser.add_argument(
        "--staging-dir", required=True, type=Path, help="Staging directory path"
    )
    args = parser.parse_args()
    sys.exit(acquire(args.url, args.staging_dir))


if __name__ == "__main__":
    main()
