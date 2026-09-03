#!/usr/bin/env python3
"""Capture one URL as a self-contained HTML page and write it to stdout.

The entry point for capturing a page outside an ingest - re-capturing a record's
frozen page after a capture bug is fixed, or capturing an archived copy when the
live page no longer serves the article. `--archive` says the URL is a replay
from a web archive: the capture gets the longer timeout an archive replay needs
and loses the archive's own toolbar, so the snapshot shows the publisher's page
and not the archive's chrome around it.

    cm run python workspace/capture_url.py <url> [--archive] > page.html
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from captures.singlefile import (  # noqa: E402
    ARCHIVE_TIMEOUT_SECONDS,
    TIMEOUT_SECONDS,
    capture_singlefile,
)

#: The Internet Archive injects its own toolbar into a replayed page.
ARCHIVE_CHROME = "#wm-ipp-base,#wm-ipp"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("url")
    ap.add_argument(
        "--archive",
        action="store_true",
        help="the URL is a web-archive replay of the page",
    )
    args = ap.parse_args()

    data = capture_singlefile(
        args.url,
        drop_selectors=ARCHIVE_CHROME if args.archive else None,
        timeout=ARCHIVE_TIMEOUT_SECONDS if args.archive else TIMEOUT_SECONDS,
    )
    if not data:
        print("capture produced nothing", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
