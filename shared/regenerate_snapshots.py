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

# A capture is accepted only if it actually contains the article, and the record
# itself is the ground truth for that: the share of the record's own vocabulary
# that appears in the capture. A paywall interstitial, a bot check or a redirect
# stub scores near zero however many bytes it weighs, and a small old page scores
# 100% on 9 KB - which no byte floor can tell apart.
MIN_ARTICLE_COVERAGE = 0.8


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


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z']{4,}", text.lower()))


def article_coverage(html: str, body: str) -> float:
    """The share of the record's own vocabulary that appears in the capture -
    a direct answer to "is this the article?", independent of page size."""
    want = _words(re.sub(r"<!--.*?-->", " ", body, flags=re.S))
    if not want:
        return 1.0
    stripped = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    got = _words(re.sub(r"<[^>]+>", " ", stripped))
    return len(want & got) / len(want)


def source_url(text: str) -> str | None:
    m = re.search(r"^source_url:\s*(\S+)", text, re.M)
    return m.group(1) if m else None


def capture(url: str, from_archive: bool = False) -> bytes | None:
    """Capture a page via the acquire container's capture_url entry point."""
    cmd = ["cm", "run", "python", "workspace/capture_url.py", url]
    if from_archive:
        cmd.append("--archive")
    result = subprocess.run(cmd, cwd=ACQUIRE, capture_output=True, timeout=1200)
    data = result.stdout
    return data if data.startswith(b"<") else None


def replace_snapshot(
    text: str, role: str, new_hash: str, when: str, source: str | None = None
) -> str:
    """Point a snapshot entry at a new capture and stamp when it was taken,
    adding the entry - and the block - when the record has none. Returns the
    text unchanged only if there is nowhere to anchor a snapshots block."""
    entry = (
        f"  - role: {role}\n"
        f"    hash: sha256:{new_hash}\n"
        f"    content_type: text/html\n"
        f"    captured_at: {when}\n"
    )
    if source:
        entry += f"    captured_from: {source}\n"
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


def regenerate(path: Path, write: bool, from_url: str | None = None) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    url = from_url or source_url(text)
    if not url:
        return "no source_url"
    body = text.split("\n---\n", 1)[1] if "\n---\n" in text else ""
    before = stored_css(text)
    data = capture(url, from_archive=bool(from_url))
    if not data:
        return "capture failed"
    html = data.decode("utf-8", "replace")
    coverage = article_coverage(html, body)
    if coverage < MIN_ARTICLE_COVERAGE:
        return (
            f"fresh capture holds only {coverage:.0%} of the record's text "
            f"({len(data) // 1024} KB) - left alone"
        )
    after = css_size(html)
    if before is None:
        verdict = f"no stored capture; fresh one has {after // 1024} KB of CSS"
    elif after < before * MIN_CSS_GAIN:
        return (
            f"stored capture is not materially worse "
            f"({before // 1024} KB of CSS, fresh {after // 1024} KB) - left alone"
        )
    else:
        verdict = f"CSS {before // 1024} KB -> {after // 1024} KB"
    verdict += f", holds {coverage:.0%} of the record's text"
    if from_url:
        verdict += " (from the archive)"
    if not write:
        return f"WOULD REPLACE: {verdict}"
    new_hash = hashlib.sha256(data).hexdigest()
    when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Update the record first: a capture archived under a hash no record points
    # at is litter nothing will ever collect.
    updated = replace_snapshot(text, "single_file", new_hash, when, from_url)
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
    ap.add_argument(
        "--from",
        dest="from_url",
        help="capture this URL instead of the record's own - an archived copy "
        "when the live page no longer serves the article. Stamps captured_from "
        "and drops the archive's own toolbar. One record at a time.",
    )
    args = ap.parse_args()

    records = web_records()
    if args.hashes:
        records = [p for p in records if any(p.stem.startswith(h) for h in args.hashes)]
        if not records:
            print("no matching records", file=sys.stderr)
            return 1

    if args.from_url and len(records) != 1:
        print("--from takes exactly one record", file=sys.stderr)
        return 1

    replaced = 0
    for path in records:
        outcome = regenerate(path, args.write, args.from_url)
        print(f"{path.stem[:12]}: {outcome}", flush=True)
        replaced += outcome.startswith("re-captured")
    if args.write:
        print(f"\n{replaced} of {len(records)} record(s) given a new capture")
    return 0


if __name__ == "__main__":
    sys.exit(main())
