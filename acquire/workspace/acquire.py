#!/usr/bin/env python3
"""Asset acquisition - fetches a URL and writes the result to a staging directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from detect import detect
from fetch import FETCHERS
from fetch.ytdlp import is_video_platform


WAYBACK_PREFIX = "https://web.archive.org/web/"


def _url_source_id(url: str) -> str:
    """Generate a stable source_id for a URL by hashing it.

    Used when no platform-specific extractor (like yt-dlp) provides one.
    Format: 'url:{12-char hash}' for readability.
    """
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return f"url:{h[:12]}"


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
