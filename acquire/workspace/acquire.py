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


def _url_source_id(url: str) -> str:
    """Generate a stable source_id for a URL by hashing it.

    Used when no platform-specific extractor (like yt-dlp) provides one.
    Format: 'url:{12-char hash}' for readability.
    """
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return f"url:{h[:12]}"


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

    Returns 0 on success, 1 on failure.
    """
    staging_dir.mkdir(parents=True, exist_ok=True)

    for method_name, fetcher in FETCHERS:
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

        manifest = {
            "source": url,
            "asset": asset_name,
            "detected_type": detected_type,
            "fetch_method": method_name,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

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

        # Fall back to URL-derived source_id if fetcher didn't provide one
        if "source_id" not in manifest:
            manifest["source_id"] = _url_source_id(url)

        (staging_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

        print(f"  {method_name}: success ({detected_type})", file=sys.stderr)
        return 0

    manifest = {
        "source": url,
        "asset": None,
        "detected_type": None,
        "fetch_method": None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "error": "All fetch methods exhausted",
    }
    (staging_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("All fetch methods exhausted", file=sys.stderr)
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
