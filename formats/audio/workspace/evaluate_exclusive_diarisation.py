#!/usr/bin/env python3
"""Compare regular and exclusive diarisation on one timestamp fixture.

This is an offline reconciliation test, not a model-quality benchmark. The
fixture carries one WhisperX word timeline, both Community-1 track sets, and
reviewed speaker labels. Both candidates pass through production alignment and
speaker-attribution scoring, so only the selected diarisation tracks vary.

Run from the repository root:

    PYTHONPATH=formats/audio/workspace python3 \
      formats/audio/workspace/evaluate_exclusive_diarisation.py <fixture.json>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from alignment.align import align
from models import Segment, SpeakerSegment
from score_attribution import score
from transcript_cache import load_raw_archive


def score_track_sets(
    segments: list[Segment], pyannote: dict, truth: dict[str, str]
) -> dict[str, dict]:
    """Score regular and exclusive tracks against one fixed word timeline."""
    out = {}
    for label, track_key in (
        ("regular", "tracks"),
        ("exclusive", "exclusive_tracks"),
    ):
        speakers = [
            SpeakerSegment(t["speaker"], t["start"], t["end"])
            for t in pyannote.get(track_key, [])
        ]
        if not speakers:
            raise ValueError(f"fixture has no pyannote.{track_key}")
        out[label] = score(align(segments, speakers, keep_words=True), truth)
    return out


def evaluate(path: Path) -> dict[str, dict]:
    data = json.loads(path.read_text())
    truth = data["reviewed_words"]
    segments, _ = load_raw_archive(path)
    return score_track_sets(segments, data["pyannote"], truth)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.fixture), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
