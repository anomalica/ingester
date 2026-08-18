"""Post-ingestion repair for a stored book record.

Applies chapter-boundary DROP-CAP fixes and re-stores the record: the body changes,
so the content_hash changes, so the record is rewritten at its new hash and the old
one retired to store/v1 (write_record force=True), exactly as a --force re-ingest
would. A drop-cap join only re-joins characters that are already in the body - it
adds no model-generated prose - and every applied join is verified to differ from
the raw join by whitespace only, so a model error cannot smuggle content in.

STRUCTURAL findings (a byline promoted to a heading, a heading at the wrong level,
a malformed marker) are REPORTED, never auto-changed: those need judgement and
Mark asked to check them, not rewrite them.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_WS = Path(__file__).resolve().parent
sys.path.insert(0, str(_WS))
sys.path.insert(0, str(_WS.parents[2] / "shared"))

from text_repair import rejoin_dropcaps  # noqa: E402
from inspect_chapters import (  # noqa: E402
    find_boundaries,
    inspect_boundary,
    needs_inspection,
)

from hashing import content_hash_label, hash_string  # noqa: E402
from record import write_record  # noqa: E402
from verification import build_sidecar, needs_sidecar, write_sidecar  # noqa: E402

_AI_SPLIT_RE = re.compile(r"(?m)^([AI])\n([a-z]+)")
_FRONTMATTER_RE = re.compile(r"^(---\n.*?\n---)\n+(.*)$", re.DOTALL)


@dataclass
class RepairResult:
    changed: bool = False
    dropcaps_fixed: int = 0
    ai_splits_fixed: int = 0
    structural: list[dict] = field(default_factory=list)  # reported, not applied
    new_hash: str | None = None
    skipped_reason: str | None = None


def split_record(text: str) -> tuple[str, str]:
    """(frontmatter-with-fences, body). Round-trips: fm + '\\n\\n' + body == text."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError("record has no YAML frontmatter")
    return m.group(1), m.group(2)


def _apply_verified_join(body: str, letter: str, cont: str, corrected: str) -> str:
    """Rejoin the first 'letter\\ncont...' split, taking ONLY the spacing decision
    from the model - whether the correction begins 'letter cont' or 'lettercont'.
    Everything the model may have appended is ignored, and only the newline between
    the two is changed, so a model reply can never rewrite the text; it can at worst
    pick the wrong space, which is a cosmetic, git-reversible error. Returns the body
    unchanged when the correction matches neither form (left for review)."""
    corrected = corrected.strip()
    spaced, joined = f"{letter} {cont}", f"{letter}{cont}"
    if corrected.startswith(spaced):
        joiner = " "
    elif corrected.startswith(joined):
        joiner = ""
    else:
        return body  # the model's decision does not fit this split - leave it
    return re.sub(
        rf"(?m)^{re.escape(letter)}\n({re.escape(cont)})",
        lambda m: f"{letter}{joiner}{m.group(1)}",
        body,
        count=1,
    )


def repair_body(body: str, call, is_reviewed: bool = False) -> tuple[str, RepairResult]:
    """Return (new_body, result). `call` is the injected model function; pass None
    to skip the A/I resolution (deterministic-only)."""
    res = RepairResult()
    if is_reviewed:
        res.skipped_reason = "human-reviewed record - not modified"
        return body, res

    # 1. Deterministic B-Z drop-cap rejoin (never A/I).
    rejoined = rejoin_dropcaps(body)
    res.dropcaps_fixed = len(re.findall(r"(?m)^[B-HJ-Z]\n[a-z]", body))
    body = rejoined

    # 2. A/I drop-caps + structural findings, boundary by boundary.
    if call is not None:
        for b in find_boundaries(body):
            if not needs_inspection(b.region):
                continue
            for iss in inspect_boundary(b.region, call):
                if iss["type"] == "dropcap_split" and iss.get("corrected"):
                    m = _AI_SPLIT_RE.search(b.region)
                    if m:
                        before = body
                        body = _apply_verified_join(
                            body, m.group(1), m.group(2), iss["corrected"]
                        )
                        if body != before:
                            res.ai_splits_fixed += 1
                        else:
                            res.structural.append(
                                {**iss, "chapter": b.index, "note": "unverified join"}
                            )
                else:
                    res.structural.append({**iss, "chapter": b.index})

    res.changed = res.dropcaps_fixed > 0 or res.ai_splits_fixed > 0
    return body, res


def restore_record(
    record_path: Path,
    new_body: str,
    store_dir: Path,
    by_name_dir: Path,
    date: str,
    title: str,
) -> str:
    """Re-store a repaired record at its new content_hash, retiring the old to v1.
    Returns the new hash."""
    frontmatter, _ = split_record(record_path.read_text())
    new_hash = hash_string(new_body)
    frontmatter = re.sub(
        r"(?m)^content_hash:.*$",
        f"content_hash: {content_hash_label(new_hash)}",
        frontmatter,
    )
    content = f"{frontmatter}\n\n{new_body}"
    write_record(
        store_dir, by_name_dir, new_hash, content, date, "ebook", title, force=True
    )
    if needs_sidecar(content):
        write_sidecar(store_dir, new_hash, build_sidecar(new_body))
    return new_hash
