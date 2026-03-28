#!/usr/bin/env python3
"""Asset acquisition - fetches a URL and writes the result to a staging directory."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from detect import detect
from fetch import FETCHERS

MIME_TO_EXT = {
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "application/pdf": ".pdf",
    "audio/mpeg": ".mp3",
    "video/mp4": ".mp4",
    "audio/wav": ".wav",
    "video/webm": ".webm",
    "audio/ogg": ".ogg",
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
