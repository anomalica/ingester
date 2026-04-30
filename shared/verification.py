"""Sidecar generation for record verification.

Produces a `{hash}.verification.json` file alongside each record. Backend-only:
the workbench server reads it to verify a reviewer has the source material,
but never exposes its contents (especially cloze answers) to the frontend.

Verification primitives:
- sha256 of the original source file (instant pass on byte match)
- size_bytes / page_count / duration (sanity bounds)
- ~30 cloze challenges drawn from the body text (proof-of-possession)
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import string
from pathlib import Path

CHALLENGE_COUNT = 30
CONTEXT_WORDS = 4
MIN_BODY_WORDS = 100

# Records with these copyright statuses can be served openly and don't need a
# verification gate. Anything else (licensed, restricted, etc.) gets a sidecar.
PUBLIC_COPYRIGHT_STATUSES = {"publicly_accessible", "public_domain"}


def needs_sidecar(content: str) -> bool:
    """Decide whether a record needs a verification sidecar.

    Reads the copyright.status field from the record's YAML frontmatter.
    Public-domain or publicly-accessible records can be served openly; any
    other status (or missing status) means we generate a sidecar to gate
    reviewer access.
    """
    block_match = re.search(
        r"^copyright:\s*\n((?:[ \t]+\S.*\n)+)", content, flags=re.MULTILINE
    )
    if not block_match:
        return True  # default to gated when copyright block is missing
    status_match = re.search(
        r"^\s+status:\s*(\S+)", block_match.group(1), flags=re.MULTILINE
    )
    if not status_match:
        return True
    status = status_match.group(1).strip().strip('"').strip("'").lower()
    return status not in PUBLIC_COPYRIGHT_STATUSES


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _strip_annotations(body: str) -> str:
    """Remove record annotations so cloze contexts are drawn from source content only.

    Strips: YAML frontmatter, HTML-comment block annotations, inline {{...}}
    annotations (used for redactions and illegibles), and line-leading audio
    timestamps like ``00:01:45.2``.
    """
    body = re.sub(r"^---\n.*?\n---\n", "", body, count=1, flags=re.DOTALL)
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.DOTALL)
    body = re.sub(r"\{\{[^{}]*\}\}", " ", body)
    body = re.sub(r"^\d{2}:\d{2}:\d{2}\.\d\s+", "", body, flags=re.MULTILINE)
    return body


def _normalise(word: str) -> str:
    return word.strip(string.punctuation + "“”‘’\"'").lower()


def _tokenise(text: str) -> list[str]:
    return [t for t in re.findall(r"\S+", text) if any(c.isalpha() for c in t)]


def _is_usable_word(raw: str) -> bool:
    norm = _normalise(raw)
    if len(norm) < 4:
        return False
    if not norm.isalpha():
        return False
    return True


def _generate_challenges(
    body: str, count: int, context_words: int, seed: str
) -> list[dict]:
    """Pick non-overlapping cloze challenges with unique contexts.

    Each challenge: {id, before, after, answer}. The `before` and `after`
    strings together form a unique context within the body so the answer is
    unambiguous when the workbench backend verifies a reviewer's response.
    """
    text = _strip_annotations(body)
    tokens = _tokenise(text)
    if len(tokens) < MIN_BODY_WORDS:
        return []

    rng = random.Random(seed)
    middle = context_words
    span_len = 2 * context_words + 1

    candidate_indices = list(range(middle, len(tokens) - middle))
    rng.shuffle(candidate_indices)

    used_ranges: list[tuple[int, int]] = []
    challenges: list[dict] = []

    for idx in candidate_indices:
        if len(challenges) >= count:
            break
        if any(start <= idx <= end for start, end in used_ranges):
            continue

        target = tokens[idx]
        if not _is_usable_word(target):
            continue

        before_tokens = tokens[idx - context_words : idx]
        after_tokens = tokens[idx + 1 : idx + 1 + context_words]
        if any(not _is_usable_word(t) for t in before_tokens + after_tokens):
            continue

        before = " ".join(before_tokens)
        after = " ".join(after_tokens)
        pattern = re.escape(before) + r"\s+(\S+)\s+" + re.escape(after)
        matches = re.findall(pattern, text, flags=re.IGNORECASE)
        if not matches:
            continue
        unique_answers = {_normalise(m) for m in matches}
        if len(unique_answers) != 1:
            continue

        challenges.append(
            {
                "id": len(challenges) + 1,
                "before": before,
                "after": after,
                "answer": _normalise(target),
            }
        )
        used_ranges.append((idx - span_len, idx + span_len))

    return challenges


def build_sidecar(
    body: str,
    source_path: Path | None = None,
    *,
    page_count: int | None = None,
    duration_seconds: float | None = None,
    seed: str | None = None,
) -> dict:
    """Build a verification sidecar dict for a record."""
    sidecar: dict = {
        "algorithm": "cloze-v1",
        "challenge_count": CHALLENGE_COUNT,
        "context_words": CONTEXT_WORDS,
    }

    if source_path is not None and source_path.exists():
        sidecar["sha256"] = _file_sha256(source_path)
        sidecar["size_bytes"] = source_path.stat().st_size

    if page_count is not None:
        sidecar["page_count"] = page_count
    if duration_seconds is not None:
        sidecar["duration_seconds"] = duration_seconds

    seed_value = (
        seed or sidecar.get("sha256") or hashlib.sha256(body.encode()).hexdigest()
    )
    sidecar["challenges"] = _generate_challenges(
        body, CHALLENGE_COUNT, CONTEXT_WORDS, seed_value
    )

    return sidecar


def write_sidecar(store_dir: Path, hex_hash: str, sidecar: dict) -> Path:
    path = store_dir / f"{hex_hash}.verification.json"
    path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n")
    return path
