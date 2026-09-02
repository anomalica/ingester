"""In-place refresh of a live web record from its own archived page.

A web record's `content_hash` is a frozen ingest-time identity (the filename),
not a live body hash, so a body re-extracted from the SAME source bytes keeps
its identity: `by-name/` symlinks, digests, review and verification sidecars
all still resolve (decision 0040, "supersession vs in-place re-extraction").
This is how an extractor improvement reaches records already in the store: the
scheduler hands `./ingest --force --source-url URL records/{source_hash}.html`
to the handler, which lands here instead of minting a second record.

What survives a refresh, and how:
- the frontmatter, apart from the extraction stamps (`date_extracted`,
  `processing.*`, `quality`);
- the stored media files - each image annotation the fresh extraction cannot
  download again is given the stored file for the same image (paired by
  alt/caption text, or by order when neither side has any);
- a reviewer's `<!-- irrelevant: start/end -->` regions, re-placed around the
  same prose in the fresh body;
- a `review_carryover` stamp on a reviewed record, so the workbench shows
  "carried over - verify" rather than a green tick over text that moved.

What stops a refresh: prose going missing. Every word of the stored body that
is absent from the fresh extraction is counted (re-ordering cancels out, a
caption that moved into an annotation still counts as present, words inside a
reviewer's irrelevant regions are exempt). A reviewed record tolerates none - a
reviewer's own text edit must never vanish silently; an unreviewed one
tolerates the furniture an improved extractor now drops, up to a small bound.
A refused refresh leaves the record untouched and fails the ingest loudly.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from pipeline_version import current_version, write_manifest
from quality import stamp_record
from record import get_version
from validator import validate
from verification import build_sidecar, needs_sidecar, write_sidecar

MEDIA_TYPE = "web"

_ANNOTATION_RE = re.compile(r"<!--.*?-->", re.S)
_IMAGE_BLOCK_RE = re.compile(r"<!--\nimage:\n(.*?)-->", re.S)
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_LEADING_HEADING_RE = re.compile(r"^\s*#[^\n]*\n")
_IRRELEVANT_START = re.compile(r"^\s*<!--\s*irrelevant:\s*start\s*-->\s*$")
_IRRELEVANT_END = re.compile(r"^\s*<!--\s*irrelevant:\s*end\s*-->\s*$")
_NON_WORD_RE = re.compile(r"[\W_]+")
# A reviewer's inline markers: paired {{X-start: ...}} / {{X-end: id}} spans
# (highlight, note, link, cites, external) plus any other {{...}} token.
_INLINE_MARKER_RE = re.compile(r"\{\{[^{}]*\}\}")
_PAIRED_MARKER_RE = re.compile(
    r"\{\{\s*(highlight|note|link|cites|external)-(start|end)\s*:\s*(.*?)\s*\}\}"
)
# A stored token at least this long whose letters run unbroken through the
# fresh prose is a jam of words, not a word that vanished.
_JAM_MIN_CHARS = 5
# Squashed characters of context used to find where a marker sat.
_CONTEXT_CHARS = (24, 16, 8)

# Below this many characters a line is too short to identify prose on its own
# (a lone "Advertisement" or a date), so it cannot anchor a ported region.
_MIN_ANCHOR_CHARS = 12
# An unreviewed record may lose this much of its stored prose to a refresh -
# furniture an improved extractor no longer extracts - before it is refused: a
# tenth of its words, never more than a footer's worth, never fewer than a few.
_UNREVIEWED_MAX_LOST_WORDS = 30
_UNREVIEWED_MIN_LOST_WORDS = 5
_UNREVIEWED_MAX_LOST_SHARE = 0.1


@dataclass
class Outcome:
    written: bool
    reason: str
    notes: list[str] = field(default_factory=list)


def trafilatura_version() -> str:
    try:
        return version("trafilatura")
    except PackageNotFoundError:
        return "unknown"


def split_record(text: str) -> tuple[str, str] | None:
    """(frontmatter, body) of a record, or None if it has no frontmatter."""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end < 0:
        return None
    return text[4:end], text[end + 5 :]


def tidy(body: str) -> str:
    """Trailing whitespace (NBSP included) off every line, one final newline -
    the shape the ingests commit hook keeps, so the body written is the body
    committed."""
    return "\n".join(line.rstrip() for line in body.rstrip("\n").split("\n")) + "\n"


# --- what the body says -------------------------------------------------------


def _annotation_words(m: re.Match) -> str:
    """The words an image annotation holds (alt and caption), so a caption that
    moved from loose prose into the annotation still counts as present."""
    return " ".join(
        v.group(1) for v in re.finditer(r"^  (?:alt|caption): (.+)$", m.group(0), re.M)
    )


def word_bag(text: str) -> Counter:
    text = _LEADING_HEADING_RE.sub("", text, count=1)
    text = _ANNOTATION_RE.sub(_annotation_words, text)
    text = _INLINE_MARKER_RE.sub("", text)
    text = _LINK_RE.sub(r"\1", text)
    return Counter(re.findall(r"[^\W_]+", text.lower()))


def _squash(line: str) -> str:
    line = _INLINE_MARKER_RE.sub("", line)
    return _NON_WORD_RE.sub("", _LINK_RE.sub(r"\1", line)).lower()


def irrelevant_regions(body: str) -> list[list[str]]:
    """The content lines of each reviewer-marked irrelevant region, in order."""
    regions: list[list[str]] = []
    current: list[str] | None = None
    for line in body.split("\n"):
        if _IRRELEVANT_START.match(line):
            current = []
        elif _IRRELEVANT_END.match(line):
            if current is not None:
                regions.append(current)
            current = None
        elif current is not None and line.strip():
            current.append(line)
    return regions


def _without_irrelevant(body: str) -> str:
    out = []
    inside = False
    for line in body.split("\n"):
        if _IRRELEVANT_START.match(line):
            inside = True
        elif _IRRELEVANT_END.match(line):
            inside = False
        elif not inside:
            out.append(line)
    return "\n".join(out)


def words_gone(old_body: str, new_body: str) -> Counter:
    """Words of the stored body that appear nowhere in the fresh one. Counted
    outside the stored body's irrelevant regions - a reviewer has already said
    that prose does not matter, so an extractor that stops emitting it loses
    nothing. A stored "word" that is really two words jammed together by the
    old extractor ("withexecutive") is not gone when the fresh prose still
    reads "with executive": its letters survive as a run."""
    old = word_bag(_without_irrelevant(old_body))
    new = word_bag(new_body)
    new_letters = _squash(_ANNOTATION_RE.sub(_annotation_words, new_body))
    return Counter(
        {
            w: n
            for w, n in old.items()
            if w not in new and not (len(w) >= _JAM_MIN_CHARS and w in new_letters)
        }
    )


# --- what carries over --------------------------------------------------------


def _image_key(block: str) -> tuple[str, str]:
    alt = re.search(r"^  alt: (.+)$", block, re.M)
    caption = re.search(r"^  caption: (.+)$", block, re.M)
    return (alt.group(1) if alt else "", caption.group(1) if caption else "")


def _image_file(block: str) -> str | None:
    m = re.search(r"^  file: (.+)$", block, re.M)
    return m.group(1) if m else None


def transplant_image_files(old_body: str, new_body: str) -> tuple[str, str]:
    """Give each fresh image annotation that has no `file:` (its download
    failed, or extraction ran offline) the stored file for the same image.

    An annotation with an alt or caption pairs with the stored annotation
    carrying the same text; annotations with neither pair by order among
    themselves. A fresh annotation with no file and no stored counterpart is
    dropped (it describes nothing the record had); a stored annotation with no
    fresh counterpart is appended at the end so its media file stays reachable.
    Returns the body and a one-line account.
    """
    old_blocks = _IMAGE_BLOCK_RE.findall(old_body)
    new_matches = list(_IMAGE_BLOCK_RE.finditer(new_body))
    if not old_blocks:
        return new_body, ""
    if not new_matches:
        return _append_blocks(new_body, old_blocks), (
            f"images: {len(old_blocks)} stored annotation(s) appended - the fresh "
            "extraction found none"
        )
    keyed = [(_image_key(b), b) for b in old_blocks if _image_key(b) != ("", "")]
    keyless = [b for b in old_blocks if _image_key(b) == ("", "")]
    pairing: list[tuple[str | None, re.Match]] = []
    for m in new_matches:
        key = _image_key(m.group(1))
        hit = None
        if key != ("", ""):
            hit = next((b for k, b in keyed if k == key), None)
            if hit is not None:
                keyed.remove((key, hit))
        elif keyless:
            hit = keyless.pop(0)
        pairing.append((hit, m))
    remaining = [b for _, b in keyed] + keyless
    dropped = sum(1 for b, m in pairing if b is None and not _image_file(m.group(1)))
    note = (
        f"images: {sum(1 for b, _ in pairing if b)} paired, {dropped} fresh dropped, "
        f"{len(remaining)} stored appended"
    )
    out = []
    last = 0
    for old, m in pairing:
        out.append(new_body[last : m.start()])
        fresh_file = _image_file(m.group(1))
        if old is None and not fresh_file:
            last = m.end()
            if new_body[last : last + 2] == "\n\n":
                last += 2
            continue
        out.append(new_body[m.start() : m.start(1)])
        stored_file = _image_file(old) if old else None
        inner = new_body[m.start(1) : m.end(1)]
        if stored_file and not fresh_file:
            out.append(f"  file: {stored_file}\n")
        elif stored_file and fresh_file != stored_file:
            # The same picture fetched again: keep the bytes already stored and
            # served, so a re-encoded download does not churn the media.
            inner = inner.replace(f"  file: {fresh_file}\n", f"  file: {stored_file}\n")
        out.append(inner)
        out.append(new_body[m.end(1) : m.end()])
        last = m.end()
    out.append(new_body[last:])
    body = "".join(out)
    if remaining:
        body = _append_blocks(body, remaining)
    return body, note


def _append_blocks(body: str, blocks: list[str]) -> str:
    return (
        body.rstrip("\n")
        + "\n\n"
        + "\n\n".join(f"<!--\nimage:\n{b}-->" for b in blocks)
        + "\n"
    )


def port_irrelevant_markers(old_body: str, new_body: str) -> tuple[str, int, int]:
    """Re-place each reviewer-marked irrelevant region around the fresh body's
    lines that carry the same prose. A fresh line matches a stored one when it
    contains it (a paragraph the old extractor split now arrives whole).
    Returns the body and the counts of regions ported and not ported (whose
    prose the fresh extraction no longer carries at all)."""
    regions = irrelevant_regions(old_body)
    if not regions:
        return new_body, 0, 0
    lines = new_body.split("\n")
    squashed = [_squash(line) for line in lines]
    taken = [False] * len(lines)
    spans: list[tuple[int, int]] = []
    unported = 0
    for region in regions:
        hits = []
        for content in region:
            key = _squash(content)
            if len(key) < _MIN_ANCHOR_CHARS:
                continue
            for i, s in enumerate(squashed):
                if (
                    not taken[i]
                    and key in s
                    and not lines[i].lstrip().startswith("<!--")
                ):
                    hits.append(i)
                    break
        if not hits:
            unported += 1
            continue
        lo, hi = min(hits), max(hits)
        for i in range(lo, hi + 1):
            taken[i] = True
        spans.append((lo, hi))
    out: list[str] = []
    starts = {lo: hi for lo, hi in spans}
    ends = {hi for _, hi in spans}
    for i, line in enumerate(lines):
        if i in starts:
            out.append("<!-- irrelevant: start -->")
            out.append("")
        out.append(line)
        if i in ends:
            out.append("")
            out.append("<!-- irrelevant: end -->")
    return "\n".join(out), len(spans), unported


def _marker_id(payload: str) -> str:
    """The id a paired marker carries: bare (`a1`) or first in a flow list
    (`[a1, "text"]`)."""
    m = re.match(r"\[\s*([^,\]]+)", payload)
    return (m.group(1) if m else payload).strip().strip("\"'")


def _prose_map(body: str) -> tuple[str, list[int]]:
    """The body squashed to its letters, with each squashed character's index in
    the raw body, skipping annotations, inline markers and link targets."""
    chars: list[str] = []
    positions: list[int] = []
    i = 0
    n = len(body)
    while i < n:
        if body.startswith("<!--", i):
            end = body.find("-->", i)
            i = n if end < 0 else end + 3
            continue
        if body.startswith("{{", i):
            end = body.find("}}", i)
            i = n if end < 0 else end + 2
            continue
        if body[i] == "]" and i + 1 < n and body[i + 1] == "(":
            end = body.find(")", i)
            i = n if end < 0 else end + 1
            continue
        c = body[i]
        if not _NON_WORD_RE.fullmatch(c) and c != "_":
            chars.append(c.lower())
            positions.append(i)
        i += 1
    return "".join(chars), positions


def _locate(squashed: str, before: str, after: str, taken_from: int) -> int | None:
    """The squashed index at which a marker with these contexts sat: where
    `before` ends and `after` begins, tried at shrinking context widths."""
    for width in _CONTEXT_CHARS:
        b, a = before[-width:], after[:width]
        if not b and not a:
            continue
        idx = squashed.find(b + a, taken_from)
        if idx >= 0:
            return idx + len(b)
    return None


def port_inline_markers(old_body: str, new_body: str) -> tuple[str, int, int]:
    """Re-place a reviewer's paired inline markers ({{highlight-start: id}} ...
    {{highlight-end: id}} and the note/link/cites/external families) at the
    same prose positions in the fresh body, found by the letters either side
    of each marker. Returns the body, the count of markers placed and the
    count of pairs dropped because one half no longer has a home."""
    old_markers = list(_PAIRED_MARKER_RE.finditer(old_body))
    if not old_markers:
        return new_body, 0, 0
    old_squashed, _ = _prose_map(old_body)
    new_squashed, new_positions = _prose_map(new_body)
    # Where each old marker sits in the old prose, as a squashed index.
    old_prose_before = _prose_map(old_body[: old_markers[0].start()])[0]
    placements: list[tuple[str, str, str, int | None]] = []  # kind, half, id, new idx
    cursor = 0
    for m in old_markers:
        idx = len(_prose_map(old_body[: m.start()])[0])
        before, after = old_squashed[:idx], old_squashed[idx:]
        at = _locate(new_squashed, before, after, cursor)
        if at is not None:
            cursor = at
        placements.append((m.group(1), m.group(2), _marker_id(m.group(3)), at))
    del old_prose_before
    # A pair lives or dies together: a start without its end would auto-close
    # at the end of the body and mark far more than the reviewer did.
    homes: dict[tuple[str, str], list[int | None]] = {}
    for kind, _half, ident, at in placements:
        homes.setdefault((kind, ident), []).append(at)
    dropped_pairs = {k for k, ats in homes.items() if any(a is None for a in ats)}
    inserts: list[tuple[int, int, str]] = []
    for m, (kind, half, ident, at) in zip(old_markers, placements):
        if (kind, ident) in dropped_pairs or at is None:
            continue
        # A start marker goes just before the prose character it opened on; an
        # end marker just after the last character it closed on, so it hugs the
        # word rather than the space or line break that follows it.
        if half == "start":
            raw = new_positions[at] if at < len(new_positions) else len(new_body)
        else:
            raw = new_positions[at - 1] + 1 if at > 0 else 0
        inserts.append((raw, 0 if half == "end" else 1, m.group(0)))
    out = new_body
    for raw, _order, marker in sorted(
        inserts, key=lambda t: (t[0], t[1]), reverse=True
    ):
        out = out[:raw] + marker + out[raw:]
    return out, len(inserts), len(dropped_pairs)


# --- the frontmatter stamps ---------------------------------------------------


def _replace_block(frontmatter: str, key: str, block: list[str]) -> str:
    """Replace the top-level mapping `key:` (and its indented lines) with
    `block`, or append the block if the key is absent."""
    lines = frontmatter.split("\n")
    out: list[str] = []
    skipping = False
    placed = False
    for line in lines:
        if line.startswith(f"{key}:"):
            skipping = True
            out.extend(block)
            placed = True
            continue
        if skipping:
            if line.startswith((" ", "\t")):
                continue
            skipping = False
        out.append(line)
    if not placed:
        out.extend(block)
    return "\n".join(out)


def _drop_block(frontmatter: str, key: str) -> str:
    lines = frontmatter.split("\n")
    out: list[str] = []
    skipping = False
    for line in lines:
        if line.startswith(f"{key}:"):
            skipping = True
            continue
        if skipping:
            if line.startswith((" ", "\t")):
                continue
            skipping = False
        out.append(line)
    return "\n".join(out)


def stamp_refusal(record_path: Path, reason: str) -> None:
    """Write a `refresh_refused` block into the record's frontmatter - the body
    is untouched - so the refusal is visible where a reviewer looks, not only
    in a job log. A later successful refresh removes it."""
    text = record_path.read_text(encoding="utf-8")
    split = split_record(text)
    if split is None:
        return
    frontmatter, body = split
    frontmatter = _replace_block(
        frontmatter,
        "refresh_refused",
        [
            "refresh_refused:",
            f"  at: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            f"  reason: {json.dumps(reason, ensure_ascii=False)}",
        ],
    )
    record_path.write_text(f"---\n{frontmatter}\n---\n{body}", encoding="utf-8")


def restamp(frontmatter: str, content_hash: str, review_carryover: bool | None) -> str:
    """The stored frontmatter with the extraction stamps brought up to date:
    date_extracted, processing (version, pipeline_version, tool version) and,
    when `review_carryover` is not None, a review_carryover block whose
    had_text_edits is that value."""
    now = datetime.now(timezone.utc)
    lines = frontmatter.split("\n")
    in_processing = False
    has_pipeline_version = False
    for i, line in enumerate(lines):
        if line.startswith("date_extracted:"):
            lines[i] = f"date_extracted: {now.isoformat()}"
        elif line.startswith("processing:"):
            in_processing = True
        elif in_processing and not line.startswith(" "):
            in_processing = False
        elif in_processing and line.startswith("  version:"):
            lines[i] = f"  version: {get_version()}"
        elif in_processing and line.startswith("  pipeline_version:"):
            lines[i] = f"  pipeline_version: {current_version(MEDIA_TYPE)}"
            has_pipeline_version = True
        elif in_processing and line.startswith("      version:"):
            lines[i] = f'      version: "{trafilatura_version()}"'
    if not has_pipeline_version:
        for i, line in enumerate(lines):
            if line.startswith("processing:"):
                lines.insert(
                    i + 1, f"  pipeline_version: {current_version(MEDIA_TYPE)}"
                )
                break
    frontmatter = _drop_block("\n".join(lines), "refresh_refused")
    if review_carryover is not None:
        frontmatter = _replace_block(
            frontmatter,
            "review_carryover",
            [
                "review_carryover:",
                f"  at: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}",
                f"  from: {content_hash}",
                f"  had_text_edits: {'true' if review_carryover else 'false'}",
            ],
        )
    return frontmatter


# --- the refresh --------------------------------------------------------------


def _declared_pipeline_version(frontmatter: str) -> int | None:
    m = re.search(r"^  pipeline_version:\s*(\d+)", frontmatter, re.M)
    return int(m.group(1)) if m else None


def _refuse(record_path: Path, reason: str, notes: list[str]) -> Outcome:
    stamp_refusal(record_path, reason)
    return Outcome(False, reason, notes)


def _reviewed(record_path: Path) -> bool:
    return record_path.with_suffix(".review.json").exists()


def refresh_record(
    record_path: Path, store_dir: Path, fresh_body: str, source_path: Path
) -> Outcome:
    """Replace the body of the live record at `record_path` with `fresh_body`,
    keeping its identity and everything a human added. See the module doc for
    what carries over and what refuses."""
    text = record_path.read_text(encoding="utf-8")
    split = split_record(text)
    if split is None:
        return Outcome(False, f"refused: {record_path.name} has no frontmatter")
    frontmatter, old_body = split
    content_hash = record_path.stem
    reviewed = _reviewed(record_path)
    notes: list[str] = []

    new_body, image_note = transplant_image_files(old_body, tidy(fresh_body))
    if image_note:
        notes.append(image_note)
    new_body, ported, unported = port_irrelevant_markers(old_body, new_body)
    if ported or unported:
        notes.append(
            f"irrelevant regions: {ported} ported"
            + (f", {unported} no longer in the extraction" if unported else "")
        )
    new_body, placed, dropped_pairs = port_inline_markers(old_body, new_body)
    if placed or dropped_pairs:
        notes.append(
            f"inline markers: {placed} re-placed"
            + (f", {dropped_pairs} pair(s) without a home" if dropped_pairs else "")
        )
    if dropped_pairs and reviewed:
        return _refuse(
            record_path,
            f"refused: {dropped_pairs} of the reviewer's marker pair(s) have no home "
            "in the fresh extraction",
            notes,
        )
    new_body = tidy(new_body)

    gone = words_gone(old_body, new_body)
    lost = sum(gone.values())
    if reviewed:
        allowed = 0
    else:
        total = sum(word_bag(_without_irrelevant(old_body)).values())
        allowed = min(
            _UNREVIEWED_MAX_LOST_WORDS,
            max(_UNREVIEWED_MIN_LOST_WORDS, int(total * _UNREVIEWED_MAX_LOST_SHARE)),
        )
    if lost > allowed:
        sample = " ".join(list(gone.elements())[:40])
        who = "a reviewed record keeps every word" if reviewed else f"bound {allowed}"
        return _refuse(
            record_path,
            f"refused: {lost} word(s) of the stored body are absent from the fresh "
            f"extraction ({who}): {sample}",
            notes,
        )
    if lost:
        notes.append(f"words no longer extracted: {lost} ({' '.join(gone.elements())})")

    if new_body == old_body:
        if _declared_pipeline_version(frontmatter) == current_version(MEDIA_TYPE):
            return Outcome(False, "unchanged", notes)
        # The body already matches; only the record's declared generation is
        # behind - bring the stamps up to date so it stops reading as stale.
        stamped = restamp(frontmatter, content_hash, review_carryover=None)
        record_path.write_text(
            stamp_record(f"---\n{stamped}\n---\n{new_body}"), encoding="utf-8"
        )
        write_manifest(store_dir)
        return Outcome(True, "body unchanged, stamps brought up to date", notes)

    prose_moved = _squash(_without_irrelevant(old_body)) != _squash(new_body)
    stamped = restamp(
        frontmatter, content_hash, review_carryover=prose_moved if reviewed else None
    )
    content = stamp_record(f"---\n{stamped}\n---\n{new_body}")
    result = validate(content, extra_required=["source_url"])
    if result.fixed:
        content = result.fixed
    notes.extend(f"validation: {w}" for w in result.warnings)
    if result.errors:
        return _refuse(
            record_path,
            "refused: the refreshed record does not validate: "
            + "; ".join(result.errors),
            notes,
        )
    record_path.write_text(content, encoding="utf-8")
    write_manifest(store_dir)
    if needs_sidecar(content):
        sidecar = build_sidecar(content, source_path=source_path)
        write_sidecar(store_dir, content_hash, sidecar)
        notes.append("verification sidecar regenerated")
    if reviewed:
        notes.append(
            "reviewed record: review_carryover stamped"
            + (" (prose moved - verify)" if prose_moved else "")
        )
    return Outcome(True, "refreshed in place", notes)
