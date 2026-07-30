"""Source-quality measurements for a record, cached in the `quality:` frontmatter block.

The block is a DERIVED CACHE, never authoritative: it is regenerated from the record
body by this module and the body wins on any disagreement. It exists for query
efficiency over the corpus, not as a second source of truth. Only measurements that
need a body read are cached; verdicts (is it garbled? is the author present?) are
derived at read time from the count or from frontmatter that sits right beside it,
because a stored verdict silently goes wrong when its threshold moves.

Measured fields (all counts/scores, never a thresholded flag):
  replacement_chars   U+FFFD count - encoding/decoding damage, unattributable between
                      a damaged source and a bad decode on our side.
  substitution_score  OCR proper-noun substitution rate per 1000 proper nouns. A scan
                      spells a name two ways ("Valensole" and "Valcnsolc"); a clean
                      text spells it one way. Reported, never a gate.
  chapter_markers     <!-- chapter: --> count (document-structure records).
  chapter_titles      <!-- chapter_title: --> count. titles/markers far below 1 means
                      claim locations resolve to a bare "ch7:" with no human anchor.
  page_anchors        printed_page marker count. 0 is a real finding (measured, none),
                      distinct from absence (the type cannot carry them).

Fields are OMITTED where the source_type cannot carry them, and PRESENT INCLUDING ZERO
where it can - so `page_anchors: 0` on a PDF reads as "measured, none found", never as
"not applicable".
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

_ANNOTATION_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_INLINE_ANNOTATION_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-zA-Z]{4,}\b")
_REPLACEMENT_CHAR = "�"

# OCR-confusable character pairs: glyphs a scanner routinely swaps. A substitution
# is only OCR-plausible if every differing character is one of these pairs.
_CONFUSABLE = {
    frozenset(pair)
    for pair in [
        ("c", "e"),
        ("c", "o"),
        ("e", "o"),
        ("e", "a"),
        ("a", "o"),
        ("i", "l"),
        ("i", "t"),
        ("i", "j"),
        ("l", "t"),
        ("l", "1"),
        ("i", "1"),
        ("t", "f"),
        ("m", "n"),
        ("n", "r"),
        ("h", "b"),
        ("h", "n"),
        ("n", "u"),
        ("o", "0"),
        ("u", "v"),
        ("v", "y"),
        ("s", "5"),
        ("g", "q"),
        ("b", "h"),
    ]
}

# Source types that can carry each structural field, for the omit-vs-zero rule.
_HAS_CHAPTERS = {"ebook"}
_HAS_PAGE_ANCHORS = {"ebook", "pdf"}


def _load_wordlist() -> set[str]:
    """The system wordlist, used only to suppress OCR false positives (a real word
    like 'Herald' near 'Harold' is not a corruption). Absent = no suppression, which
    only makes the score slightly noisier; it never fails."""
    for candidate in ("/usr/share/dict/words", "/usr/share/dict/american-english"):
        path = Path(candidate)
        if path.exists():
            return {
                w.strip().lower()
                for w in path.read_text(encoding="utf-8", errors="replace").splitlines()
            }
    return set()


_WORDS = _load_wordlist()


def strip_annotations(body: str) -> str:
    """Body text with block and inline annotations removed, for text measurements."""
    return _INLINE_ANNOTATION_RE.sub(" ", _ANNOTATION_RE.sub(" ", body))


def _is_ocr_variant(rare: str, common: str) -> bool:
    """True if `rare` is `common` with 1-2 OCR-confusable character substitutions."""
    if len(rare) != len(common) or rare == common:
        return False
    diffs = [(a, b) for a, b in zip(rare, common) if a != b]
    if not 1 <= len(diffs) <= 2:
        return False
    return all(frozenset((a.lower(), b.lower())) in _CONFUSABLE for a, b in diffs)


def substitution_score(text: str) -> tuple[float, list[str]]:
    """OCR proper-noun substitution rate per 1000 proper nouns, plus example pairs.

    A proper noun spelled rarely (<=2 times) is a corruption candidate; if a
    more-frequent proper noun is an OCR-confusable variant of it, it is a likely
    substitution. The rare spelling must be a non-word when a wordlist is available,
    so a real name that merely resembles another (Harold/Herald) is not flagged."""
    nouns = _PROPER_NOUN_RE.findall(text)
    if not nouns:
        return 0.0, []
    freq = Counter(nouns)
    frequent = [n for n, c in freq.items() if c >= 2]
    flagged: list[str] = []
    for noun, count in freq.items():
        if count > 2 or (_WORDS and noun.lower() in _WORDS):
            continue
        for other in frequent:
            if freq[other] > count and _is_ocr_variant(noun, other):
                flagged.append(f"{noun}->{other}")
                break
    rate = round(len(flagged) / len(nouns) * 1000, 2)
    return rate, flagged


def measure(body: str, source_type: str) -> dict:
    """Compute the quality measurements applicable to this record."""
    text = strip_annotations(body)
    score, _examples = substitution_score(text)
    result: dict[str, object] = {
        "replacement_chars": text.count(_REPLACEMENT_CHAR),
        "substitution_score": score,
    }
    if source_type in _HAS_CHAPTERS:
        result["chapter_markers"] = len(re.findall(r"<!--\s*chapter:", body))
        result["chapter_titles"] = len(re.findall(r"<!--\s*chapter_title:", body))
    if source_type in _HAS_PAGE_ANCHORS:
        result["page_anchors"] = len(re.findall(r"printed_page:", body))
    return result


def substitution_examples(body: str, limit: int = 6) -> list[str]:
    """Example corrupt->correct pairs, for a flagged record's audit trail."""
    _score, flagged = substitution_score(strip_annotations(body))
    # De-duplicate while preserving order.
    seen: dict[str, None] = {}
    for pair in flagged:
        seen.setdefault(pair, None)
    return list(seen)[:limit]


def render_block(measurements: dict) -> str:
    """Render the measurements as a `quality:` YAML frontmatter block."""
    lines = ["quality:"]
    for key, value in measurements.items():
        lines.append(f"  {key}: {value}")
    return "\n".join(lines)


def _split_record(text: str) -> tuple[str, str] | None:
    """Split a record into (frontmatter, body). None if it has no frontmatter."""
    if not text.startswith("---"):
        return None
    parts = text.split("\n---\n", 1)
    if len(parts) != 2:
        return None
    return parts[0][4:], parts[1]  # drop the leading '---\n'


def _field(frontmatter: str, key: str) -> str | None:
    match = re.search(rf"^{key}:\s*(.*)$", frontmatter, re.MULTILINE)
    return match.group(1).strip().strip('"') if match else None


def stamp_record(text: str) -> str:
    """Return the record with a freshly-computed `quality:` block in its frontmatter,
    replacing any existing one. The content_hash never covers frontmatter, so this
    changes no hash and orphans no digest. Returns the text unchanged if it has no
    frontmatter or no source_type."""
    split = _split_record(text)
    if split is None:
        return text
    frontmatter, body = split
    source_type = _field(frontmatter, "source_type")
    if not source_type:
        return text

    # Drop any existing quality block (the whole `quality:` mapping).
    fm_lines = frontmatter.splitlines()
    cleaned: list[str] = []
    skipping = False
    for line in fm_lines:
        if line.startswith("quality:"):
            skipping = True
            continue
        if skipping:
            if line.startswith(("  ", "\t")):
                continue
            skipping = False
        cleaned.append(line)

    block = render_block(measure(body, source_type))
    new_frontmatter = "\n".join(cleaned).rstrip() + "\n" + block
    return f"---\n{new_frontmatter}\n---\n{body}"


def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: quality.py stamp <record.md> | backfill <store_dir>",
            file=sys.stderr,
        )
        return 2
    command = argv[1]
    if command == "stamp":
        path = Path(argv[2])
        path.write_text(
            stamp_record(path.read_text(encoding="utf-8")), encoding="utf-8"
        )
        return 0
    if command == "backfill":
        store = Path(argv[2])
        changed = 0
        for record in sorted(store.glob("*.md")):
            original = record.read_text(encoding="utf-8", errors="replace")
            stamped = stamp_record(original)
            if stamped != original:
                record.write_text(stamped, encoding="utf-8")
                changed += 1
        print(f"stamped {changed} record(s)")
        return 0
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
