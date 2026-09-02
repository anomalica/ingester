#!/usr/bin/env python3
"""Re-extract the bodies of live web records in place from their archived HTML.

A web record's content_hash is a frozen ingest-time identity (the filename), not
a live body hash, so a body refreshed from the same archived page keeps its
identity: records/ symlinks, digests and sidecars all still resolve. This tool
exists for extractor fixes that change how the SAME source renders - here, the
emphasis handling that mangled markers into the text and re-ordered sentence
fragments around bold and italic spans.

Reviewed records (a .review.json sidecar) are never re-extracted: their review
coverage is line-addressed and their bodies may carry hand-placed markers. They
are offered a line-preserving clean instead (--strip-reviewed), which removes the
emphasis markers and nothing else.

Runs inside the webpage container:

    cm run regenerate -- --ingester-version $(git rev-parse --short HEAD)           # dry run
    cm run regenerate -- --ingester-version $(git rev-parse --short HEAD) --write
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from quality import stamp_record
from validator import validate

from extraction.trafilatura_ext import extract_article

DEFAULT_STORE = Path("/mnt/output/store")
DEFAULT_RECORDS = Path("/mnt/records")

_ANNOTATION_RE = re.compile(r"<!--.*?-->", re.S)
_IMAGE_BLOCK_RE = re.compile(r"<!--\nimage:\n(.*?)-->", re.S)
_SQUASH_RE = re.compile(r"[\s*_#>\\\-]+")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_LEADING_HEADING_RE = re.compile(r"^\s*#[^\n]*\n")
_LIST_BULLET_RE = re.compile(r"^\s*\*\s+\S")
_RULE_RE = re.compile(r"^\s*\*{3,}\s*$")
# An unescaped run of emphasis markers (a backslash-escaped `\*` is literal text).
_MARKER_RE = re.compile(r"(?<!\\)(?:\*+|__)")
# A marker that sat between the end of one word and the start of the next
# ("Office:**Still", "”*Sullivan") was standing in for the space trafilatura
# dropped; removing it must put the space back.
_JAM_BEFORE = re.compile(r"[\w:;,.!?”’)]$")
_JAM_AFTER = re.compile(r"^[\w“‘(]")


@dataclass
class Record:
    path: Path
    frontmatter: str
    body: str

    def field(self, key: str) -> str | None:
        m = re.search(rf"^{re.escape(key)}:[ \t]*(.*)$", self.frontmatter, re.M)
        if not m:
            return None
        return m.group(1).strip().strip('"')

    @property
    def reviewed(self) -> bool:
        return self.path.with_suffix(".review.json").exists()


def read_record(path: Path) -> Record | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    return Record(path, text[4:end], text[end + 5 :])


def squash(text: str) -> str:
    """Prose with everything that is not a letter of the text removed: whitespace,
    emphasis, heading and list markers, link targets, annotations and a leading
    title heading. Two bodies squashing equal carry the same words in the same
    order."""
    text = _LEADING_HEADING_RE.sub("", text, count=1)
    text = _ANNOTATION_RE.sub("", text)
    text = _LINK_RE.sub(r"\1", text)
    return _SQUASH_RE.sub("", text)


def _annotation_text(m: re.Match) -> str:
    """An image annotation's alt and caption - the words it holds - so a caption
    that moved from loose prose into the annotation does not count as lost."""
    return " ".join(
        v.group(1) for v in re.finditer(r"^  (?:alt|caption): (.+)$", m.group(0), re.M)
    )


def word_bag(text: str) -> Counter:
    text = _LEADING_HEADING_RE.sub("", text, count=1)
    text = _ANNOTATION_RE.sub(_annotation_text, text)
    text = _LINK_RE.sub(r"\1", text)
    return Counter(re.findall(r"[^\W_]+", text.lower()))


def _image_key(block: str) -> tuple[str, str]:
    alt = re.search(r"^  alt: (.+)$", block, re.M)
    caption = re.search(r"^  caption: (.+)$", block, re.M)
    return (alt.group(1) if alt else "", caption.group(1) if caption else "")


def _image_file(block: str) -> str | None:
    m = re.search(r"^  file: (.+)$", block, re.M)
    return m.group(1) if m else None


def transplant_image_files(old_body: str, new_body: str) -> tuple[str, str]:
    """Carry the stored media files into the fresh body's image annotations.

    Annotations pair by order when both bodies carry the same number, else by
    alt/caption text. A fresh annotation with no stored counterpart is dropped
    (it has no file and describes nothing the record had); a stored annotation
    with no fresh counterpart is appended verbatim at the end so its media file
    stays reachable. Returns the body and a one-line account of what happened.
    """
    old_blocks = _IMAGE_BLOCK_RE.findall(old_body)
    new_matches = list(_IMAGE_BLOCK_RE.finditer(new_body))
    remaining: list[str] = []
    if len(old_blocks) == len(new_matches):
        pairing = list(zip(old_blocks, new_matches))
        note = f"images {len(pairing)} paired by order"
    else:
        remaining = list(old_blocks)
        pairing = []
        for m in new_matches:
            key = _image_key(m.group(1))
            hit = next((b for b in remaining if _image_key(b) == key), None)
            pairing.append((hit, m))
            if hit is not None:
                remaining.remove(hit)
        note = (
            f"images {sum(1 for b, _ in pairing if b)} paired by alt/caption, "
            f"{sum(1 for b, _ in pairing if not b)} fresh dropped, "
            f"{len(remaining)} stored appended"
        )
    out = []
    last = 0
    for old, m in pairing:
        out.append(new_body[last : m.start()])
        if old is None:
            last = m.end()
            if new_body[last : last + 2] == "\n\n":
                last += 2
            continue
        file = _image_file(old)
        out.append(new_body[m.start() : m.start(1)])
        if file and not _image_file(m.group(1)):
            out.append(f"  file: {file}\n")
        out.append(new_body[m.start(1) : m.end()])
        last = m.end()
    out.append(new_body[last:])
    body = "".join(out)
    if remaining:
        body = (
            body.rstrip("\n")
            + "\n\n"
            + "\n\n".join(f"<!--\nimage:\n{b}-->" for b in remaining)
            + "\n"
        )
    return body, note


def _trafilatura_version() -> str:
    try:
        return version("trafilatura")
    except PackageNotFoundError:
        return "unknown"


def restamp_frontmatter(frontmatter: str, ingester_version: str) -> str:
    lines = frontmatter.split("\n")
    now = datetime.now(timezone.utc).isoformat()
    in_processing = False
    for i, line in enumerate(lines):
        if line.startswith("date_extracted:"):
            lines[i] = f"date_extracted: {now}"
        elif line.startswith("processing:"):
            in_processing = True
        elif in_processing and not line.startswith(" "):
            in_processing = False
        elif in_processing and line.startswith("  version:"):
            lines[i] = f"  version: {ingester_version}"
        elif in_processing and line.startswith("      version:"):
            lines[i] = f'      version: "{_trafilatura_version()}"'
    return "\n".join(lines)


def strip_emphasis_markers(body: str) -> str:
    """Remove markdown emphasis markers from prose lines without touching line
    structure, annotations, list bullets or horizontal rules."""
    out = []
    in_annotation = False
    for line in body.split("\n"):
        if in_annotation:
            out.append(line)
            if "-->" in line:
                in_annotation = False
            continue
        if line.lstrip().startswith("<!--"):
            out.append(line)
            in_annotation = "-->" not in line
            continue
        if _LIST_BULLET_RE.match(line) or _RULE_RE.match(line):
            out.append(line)
            continue
        cleaned = _MARKER_RE.sub(_marker_replacement, line)
        if cleaned != line:
            cleaned = re.sub(r" {2,}", " ", cleaned).strip()
        out.append(cleaned)
    return "\n".join(out)


def _marker_replacement(m: re.Match) -> str:
    before = m.string[: m.start()]
    after = m.string[m.end() :]
    if _JAM_BEFORE.search(before) and _JAM_AFTER.match(after):
        return " "
    return ""


def _tidy(text: str) -> str:
    """Trailing whitespace off every line (the ingests commit hook trims it, so
    the body written here must already match what lands in the repository)."""
    return "\n".join(line.rstrip() for line in text.rstrip("\n").split("\n")) + "\n"


def _write(record: Record, frontmatter: str, body: str) -> list[str]:
    content = f"---\n{frontmatter}\n---\n{body}"
    if not content.endswith("\n"):
        content += "\n"
    content = stamp_record(content)
    result = validate(content, extra_required=["source_url"])
    if result.fixed:
        content = result.fixed
    record.path.write_text(content, encoding="utf-8")
    return [*result.warnings, *(f"ERROR {e}" for e in result.errors)]


def _bag_summary(old: str, new: str) -> tuple[int, list[str]]:
    """Words of the stored body absent from the fresh one, and vice versa.
    Re-ordered fragments cancel out. A word the fresh body still has somewhere
    counts as a lost COPY (a de-duplicated caption), not a lost word; the gate
    is on words gone entirely."""
    old_bag, new_bag = word_bag(old), word_bag(new)
    gone = {w: n for w, n in old_bag.items() if w not in new_bag}
    copies = sum((old_bag - new_bag).values()) - sum(gone.values())
    gained = new_bag - old_bag
    out = []
    if gone:
        out.append(
            f"      lost {sum(gone.values())}: "
            + " ".join(w for w, n in gone.items() for _ in range(n))
        )
    if copies:
        out.append(f"      lost copies {copies} (words the fresh body still has)")
    if gained:
        out.append(
            f"      gained {sum(gained.values())}: "
            + " ".join(list(gained.elements())[:40])
        )
    return sum(gone.values()), out


def regenerate(
    record: Record,
    records_dir: Path,
    ingester_version: str,
    write: bool,
    max_lost: int,
    fetch_images: bool,
    print_body: bool,
) -> str:
    source_hash = (record.field("source_hash") or "").removeprefix("sha256:")
    html_path = records_dir / f"{source_hash}.html"
    if not source_hash or not html_path.exists():
        return f"SKIP no archived HTML ({html_path.name})"
    html = html_path.read_text(encoding="utf-8", errors="replace")
    article = extract_article(
        html,
        record.field("source_url"),
        fetch=None if fetch_images else (lambda url: None),
    )
    if article is None:
        return "SKIP extraction returned nothing"
    new_body = _tidy(article.text)
    image_note = ""
    if not fetch_images:
        new_body, image_note = transplant_image_files(record.body, new_body)
    if new_body == record.body:
        return "SAME"
    old_stars = record.body.count("*")
    new_stars = new_body.count("*")
    same_prose = squash(record.body) == squash(new_body)
    status = "EQUAL-PROSE" if same_prose else "DIFF-PROSE"
    detail = [
        f"{status} stars {old_stars}->{new_stars} lines "
        f"{len(record.body.splitlines())}->{len(new_body.splitlines())}"
        + (f"; {image_note}" if image_note else "")
    ]
    lost = 0
    if not same_prose:
        lost, summary = _bag_summary(record.body, new_body)
        detail.extend(summary)
    if print_body:
        detail.append("@@BODY\n" + new_body + "@@END")
    if write and lost <= max_lost:
        notes = _write(
            record, restamp_frontmatter(record.frontmatter, ingester_version), new_body
        )
        if fetch_images and article.media:
            media_dir = record.path.parent.parent / "media" / record.path.stem
            media_dir.mkdir(parents=True, exist_ok=True)
            for img in article.media:
                (media_dir / f"{img.img_hash}.{img.ext}").write_bytes(img.data)
        detail[0] += " WRITTEN"
        detail.extend(f"      {n}" for n in notes)
    return "\n".join(detail)


def strip_reviewed(record: Record, ingester_version: str, write: bool) -> str:
    new_body = strip_emphasis_markers(record.body)
    if new_body == record.body:
        return "SAME"
    changed = [
        (o, n) for o, n in zip(record.body.split("\n"), new_body.split("\n")) if o != n
    ]
    detail = [
        f"STRIPPED stars {record.body.count('*')}->{new_body.count('*')} lines changed {len(changed)}"
    ]
    for o, n in changed:
        detail.append(f"      - {o[:110]!r}")
        detail.append(f"      + {n[:110]!r}")
    if write:
        notes = _write(record, record.frontmatter, new_body)
        detail[0] += " WRITTEN"
        detail.extend(f"      {n}" for n in notes)
    return "\n".join(detail)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--store", type=Path, default=DEFAULT_STORE)
    ap.add_argument("--records", type=Path, default=DEFAULT_RECORDS)
    ap.add_argument("--ingester-version", required=True)
    ap.add_argument("--only", nargs="*", default=[], help="content hash prefixes")
    ap.add_argument(
        "--write", action="store_true", help="write changes (default: report)"
    )
    ap.add_argument(
        "--max-lost",
        type=int,
        default=0,
        help="write a record only if at most this many words of its stored prose "
        "are absent from the fresh body (default 0; furniture the extractor now "
        "drops counts, so inspect the dry run's 'lost' lines first)",
    )
    ap.add_argument(
        "--print-body",
        action="store_true",
        help="print each fresh body in full (for inspecting one record)",
    )
    ap.add_argument(
        "--fetch-images",
        action="store_true",
        help="download images afresh instead of keeping the stored media files",
    )
    ap.add_argument(
        "--strip-reviewed",
        action="store_true",
        help="for reviewed records, strip emphasis markers line-for-line instead of skipping",
    )
    args = ap.parse_args()

    paths = sorted(args.store.glob("*.md"))
    if args.only:
        paths = [p for p in paths if any(p.stem.startswith(o) for o in args.only)]
    counts: dict[str, int] = {}
    for path in paths:
        record = read_record(path)
        if record is None or record.field("source_type") != "web":
            continue
        if record.field("document_type") == "email":
            continue
        if record.reviewed:
            outcome = (
                strip_reviewed(record, args.ingester_version, args.write)
                if args.strip_reviewed
                else "SKIP reviewed"
            )
        else:
            outcome = regenerate(
                record,
                args.records,
                args.ingester_version,
                args.write,
                args.max_lost,
                args.fetch_images,
                args.print_body,
            )
        head = outcome.split("\n")[0].split(" ")[0]
        counts[head] = counts.get(head, 0) + 1
        title = (record.field("title") or "")[:60]
        print(f"{path.stem[:12]} {title}\n   {outcome}")
    print("\nsummary:", ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
