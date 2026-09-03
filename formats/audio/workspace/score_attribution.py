#!/usr/bin/env python3
"""Measure speaker misattribution against a reviewed record.

Speaker attribution is the largest known source of wrongly-attributed claims,
so a change to diarisation or alignment has to be measured rather than judged
by eye. This scores a candidate against records a human has reviewed, which are
the only ground truth we have.

The method matters as much as the number, and three things in it are not
obvious:

- SCORE FROM THE CACHED TRANSCRIPT, not from a fresh run. Every candidate is
  then scored on one identical transcription, so transcription quality is not a
  confound and the only thing that varies is which speaker each word was given.
  A word is keyed by its timestamp, so no text alignment is needed and the
  comparison is exact rather than fuzzy.

- `[irrelevant]` IS NOT A SPEAKER. A reviewer marks a region irrelevant to say
  it is not content - a cold-open teaser, a sponsor read - and the diarisation
  underneath it usually named the right person. Counting those words as
  misattribution measures the reviewer's edits instead of the model's errors,
  and they are the longest error runs in the corpus, so leaving them in points
  effort at the wrong problem.

- LABEL MAPPING IS MANY-TO-ONE. A model that splits one person into two labels
  has not misattributed anything; a reviewer renaming both to the same person is
  the obvious rename. One-to-one mapping charges the whole span of the second
  label as error and hides where attribution actually fails.

Usage (inside the audio container, where the workspace modules import):

    cm run python workspace/score_attribution.py <record-hash> [<record-hash> ...]

A record hash is the stem of a reviewed record in the ingests store that has a
transcript archive beside its audio in records/.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "alignment"))

from alignment.align import align  # noqa: E402
from models import Segment, SpeakerSegment, Word  # noqa: E402

RECORDS = Path("/mnt/records")
STORE = Path("/mnt/output/store")

_SPEAKER_RE = re.compile(r"^\s*<!--\s*speaker:\s*(.*?)\s*-->\s*$")
_WORD_RE = re.compile(r"\{\{t:([0-9.]+)\}\}(\S+)")

#: Reviewer region markers that are not speaker identities. See the module doc.
NOT_A_SPEAKER = {"[irrelevant]"}


def reviewed_words(record_path: Path) -> dict[str, str]:
    """``{word timestamp: reviewed speaker}``, excluding regions the reviewer
    marked as not content."""
    text = record_path.read_text(encoding="utf-8")
    body = text.split("\n---\n", 1)[1] if text.startswith("---") else text
    out: dict[str, str] = {}
    speaker: str | None = None
    for line in body.split("\n"):
        marker = _SPEAKER_RE.match(line)
        if marker:
            speaker = marker.group(1)
            continue
        if speaker is None or speaker in NOT_A_SPEAKER:
            continue
        for timestamp, _word in _WORD_RE.findall(line):
            out[timestamp] = speaker
    return out


def from_archive(stem: str) -> tuple[list[Segment], list[SpeakerSegment]]:
    """The cached transcription and diarisation for a record."""
    data = json.loads((RECORDS / f"{stem}.transcript.json").read_text())
    segments = [
        Segment(
            text=seg["text"].strip(),
            start=seg["start"],
            end=seg["end"],
            words=[
                Word(text=w["word"].strip(), start=w["start"], end=w["end"])
                for w in seg.get("words", [])
                if "start" in w and "end" in w
            ],
        )
        for seg in data["whisperx"]["aligned"]["segments"]
    ]
    speakers = [
        SpeakerSegment(speaker=t["speaker"], start=t["start"], end=t["end"])
        for t in data["pyannote"].get("tracks", [])
    ]
    return segments, speakers


def label_mapping(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """Each candidate label mapped to the reviewed name it most often covers,
    many-to-one. See the module doc for why it is not one-to-one."""
    counts: dict[str, Counter] = defaultdict(Counter)
    for candidate, reviewed in pairs:
        counts[candidate][reviewed] += 1
    return {label: c.most_common(1)[0][0] for label, c in counts.items()}


def score(turns, truth: dict[str, str]) -> dict:
    """Words and turns whose speaker disagrees with the reviewed record."""
    words = [
        (f"{w.start:.2f}", turn.speaker)
        for turn in turns
        for sentence in turn.sentences
        for w in (sentence.words or [])
    ]
    pairs = [(speaker, truth[t]) for t, speaker in words if t in truth]
    if not pairs:
        return {"matched_words": 0}
    mapping = label_mapping(pairs)
    wrong_words = sum(1 for candidate, real in pairs if mapping[candidate] != real)

    turns_total = turns_wrong = 0
    for turn in turns:
        reviewed = [
            truth[f"{w.start:.2f}"]
            for s in turn.sentences
            for w in (s.words or [])
            if f"{w.start:.2f}" in truth
        ]
        if not reviewed:
            continue
        turns_total += 1
        if mapping.get(turn.speaker) != Counter(reviewed).most_common(1)[0][0]:
            turns_wrong += 1

    return {
        "matched_words": len(pairs),
        "wrong_words": wrong_words,
        "word_error_pct": round(100 * wrong_words / len(pairs), 2),
        "turns": turns_total,
        "wrong_turns": turns_wrong,
        "turn_error_pct": round(100 * turns_wrong / turns_total, 2)
        if turns_total
        else None,
        "labels": len(mapping),
    }


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__.strip().split("\n\n")[-1], file=sys.stderr)
        return 1
    totals = [0, 0]
    for stem in argv:
        record = STORE / f"{stem}.v2.md"
        if not record.exists():
            record = STORE / f"{stem}.md"
        if not record.exists():
            print(f"{stem[:12]}: no record in the store", file=sys.stderr)
            continue
        truth = reviewed_words(record)
        if not truth:
            print(f"{stem[:12]}: no word timings to score against", file=sys.stderr)
            continue
        segments, speakers = from_archive(stem)
        result = score(align(segments, speakers, keep_words=True), truth)
        totals[0] += result["wrong_words"]
        totals[1] += result["matched_words"]
        print(
            f"{stem[:12]}  words {result['wrong_words']}/{result['matched_words']}"
            f" ({result['word_error_pct']}%)  turns {result['wrong_turns']}/{result['turns']}"
            f" ({result['turn_error_pct']}%)  labels={result['labels']}"
        )
    if totals[1]:
        print(
            f"TOTAL     words {totals[0]}/{totals[1]} ({100 * totals[0] / totals[1]:.2f}%)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
