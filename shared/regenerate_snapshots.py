#!/usr/bin/env python3
"""Re-capture a web record's visual snapshots without touching its body.

A record's `snapshots` are captures of the page as a reviewer sees it - the
frozen self-contained HTML and the PDF render. They are the review surface, and
they age differently from the extraction: a capture bug is found and fixed long
after the text was extracted correctly. Re-capturing is therefore a distinct
operation from re-ingesting, and this tool does only the former: the body, the
`content_hash` and every other field stay exactly as they are, so no identity
rotates, no digest is orphaned and no review is disturbed.

The page is fetched LIVE, because a frozen page can only be built from a live
page - the archived raw HTML names external stylesheets and images that no
longer resolve offline. The re-captured snapshot is therefore the site as it
stands today, not as it stood at ingest, and it says so: a regenerated entry
carries `captured_at`. The ingest-time DOM is unaffected and still archived
under `source_hash`.

Runs on the host; the capture itself runs in the acquire container.

    python3 shared/regenerate_snapshots.py --check            # what is broken
    python3 shared/regenerate_snapshots.py <hash> [<hash>...]  # re-capture those
    python3 shared/regenerate_snapshots.py --check --write     # re-capture all broken
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

INGESTER = Path(__file__).resolve().parent.parent
INGESTS = INGESTER.parent / "ingests"
RECORDS = INGESTER.parent / "records"
ACQUIRE = INGESTER / "acquire"

# A stored capture is replaced only when a fresh one carries materially more
# styling than it does. There is no threshold for "unstyled" that holds across
# sites - a plain 2001 page legitimately has almost no CSS, while a Squarespace
# page missing its whole layout stylesheet still has 138 KB of component CSS -
# so the test is a comparison against what the fixed capture actually produces,
# not a guess at what a good one looks like. A capture that was already fine
# keeps the file it has, and the record is not touched.
MIN_CSS_GAIN = 1.5

# A record with no stored capture has nothing to compare against, so the fresh
# one has to stand on its own. A paywall interstitial or a bot-check page comes
# back as a few KB with almost no text; a real article does not. Both floors
# must be cleared before such a capture is accepted.
MIN_NEW_BYTES = 60_000
MIN_NEW_TEXT_CHARS = 1_500

_STYLE_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.S)
_SNAPSHOT_BLOCK_RE = re.compile(r"^snapshots:\n((?:  [ -].*\n)+)", re.M)


def css_size(html: str) -> int:
    """Characters of inlined CSS in a capture - what it lost when a stylesheet
    failed to inline."""
    return sum(len(block) for block in _STYLE_RE.findall(html))


def web_records() -> list[Path]:
    out = []
    for path in sorted((INGESTS / "store").glob("*.md")):
        head = path.read_text(encoding="utf-8", errors="replace")[:4000]
        if re.search(r"^source_type: web", head, re.M) and not re.search(
            r"^superseded_by:", head, re.M
        ):
            out.append(path)
    return out


def snapshot_hash(text: str, role: str) -> str | None:
    block = _SNAPSHOT_BLOCK_RE.search(text)
    if not block:
        return None
    entries = re.findall(
        r"- role: (\S+)\n\s+hash: (?:sha256:)?([0-9a-f]{64})", block.group(1)
    )
    return next((h for r, h in entries if r == role), None)


def visible_text_chars(html: str) -> int:
    """Roughly how much readable text a capture carries, with script, style and
    tags removed - enough to tell an article from a bot-check page."""
    stripped = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    return len(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", stripped)).strip())


def source_url(text: str) -> str | None:
    m = re.search(r"^source_url:\s*(\S+)", text, re.M)
    return m.group(1) if m else None


def capture(url: str) -> bytes | None:
    """Run the acquire container's single-file capture against a live URL."""
    script = (
        "import sys; sys.path.insert(0, '/home/mark/workspace')\n"
        "from captures.singlefile import capture_singlefile\n"
        "out = capture_singlefile(sys.argv[1])\n"
        "sys.stdout.buffer.write(out or b'')\n"
    )
    result = subprocess.run(
        ["cm", "run", "python", "-c", script, url],
        cwd=ACQUIRE,
        capture_output=True,
        timeout=600,
    )
    data = result.stdout
    return data if data.startswith(b"<") else None


def replace_snapshot(text: str, role: str, new_hash: str, when: str) -> str:
    """Point a snapshot entry at a new capture and stamp when it was taken,
    adding the entry - and the block - when the record has none. Returns the
    text unchanged only if there is nowhere to anchor a snapshots block."""
    entry = (
        f"  - role: {role}\n"
        f"    hash: sha256:{new_hash}\n"
        f"    content_type: text/html\n"
        f"    captured_at: {when}\n"
    )
    block = _SNAPSHOT_BLOCK_RE.search(text)
    if block is None:
        anchor = re.search(r"^source_hash:.*\n", text, re.M) or re.search(
            r"^content_hash:.*\n", text, re.M
        )
        if not anchor:
            return text
        return text[: anchor.end()] + "snapshots:\n" + entry + text[anchor.end() :]

    existing = re.compile(
        rf"  - role: {re.escape(role)}\n(?:    .*\n)+",
    )
    if existing.search(block.group(1)):
        replaced = existing.sub(entry, block.group(1), count=1)
    else:
        replaced = block.group(1) + entry
    return text[: block.start()] + "snapshots:\n" + replaced + text[block.end() :]


def stored_css(text: str) -> int | None:
    """Characters of CSS in the record's current frozen capture, or None when it
    has no capture on disk."""
    h = snapshot_hash(text, "single_file")
    if not h:
        return None
    path = RECORDS / f"{h}.html"
    if not path.exists():
        return None
    return css_size(path.read_text(encoding="utf-8", errors="replace"))


def regenerate(path: Path, write: bool) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    url = source_url(text)
    if not url:
        return "no source_url"
    before = stored_css(text)
    data = capture(url)
    if not data:
        return "capture failed"
    html = data.decode("utf-8", "replace")
    after = css_size(html)
    if before is None:
        text_chars = visible_text_chars(html)
        if len(data) < MIN_NEW_BYTES or text_chars < MIN_NEW_TEXT_CHARS:
            return (
                f"fresh capture does not look like the article "
                f"({len(data) // 1024} KB, {text_chars} chars of text) - left alone"
            )
        verdict = f"no stored capture; fresh one has {after // 1024} KB of CSS"
    elif after < before * MIN_CSS_GAIN:
        return (
            f"stored capture is not materially worse "
            f"({before // 1024} KB of CSS, fresh {after // 1024} KB) - left alone"
        )
    else:
        verdict = f"CSS {before // 1024} KB -> {after // 1024} KB"
    if not write:
        return f"WOULD REPLACE: {verdict}"
    new_hash = hashlib.sha256(data).hexdigest()
    when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Update the record first: a capture archived under a hash no record points
    # at is litter nothing will ever collect.
    updated = replace_snapshot(text, "single_file", new_hash, when)
    if updated == text:
        return "nowhere to anchor a snapshots block - nothing written"
    (RECORDS / f"{new_hash}.html").write_bytes(data)
    path.write_text(updated, encoding="utf-8")
    return f"re-captured: {verdict}, {len(data) // 1024} KB -> {new_hash[:12]}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "hashes", nargs="*", help="record hashes (or prefixes); all if none"
    )
    ap.add_argument(
        "--write",
        action="store_true",
        help="keep the fresh capture where it is materially better (default: report only)",
    )
    args = ap.parse_args()

    records = web_records()
    if args.hashes:
        records = [p for p in records if any(p.stem.startswith(h) for h in args.hashes)]
        if not records:
            print("no matching records", file=sys.stderr)
            return 1

    replaced = 0
    for path in records:
        outcome = regenerate(path, args.write)
        print(f"{path.stem[:12]}: {outcome}", flush=True)
        replaced += outcome.startswith("re-captured")
    if args.write:
        print(f"\n{replaced} of {len(records)} record(s) given a new capture")
    return 0


if __name__ == "__main__":
    sys.exit(main())
