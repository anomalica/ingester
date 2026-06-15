"""Cache the expensive transcription + diarisation output beside the record so
a record can be re-rendered into a different output format later without
re-running the GPU. The cache is format-independent (it holds the raw segments
and speaker segments, before alignment), so changing the record format only
needs a cheap CPU re-render."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from models import Segment, SpeakerSegment, Word

CACHE_SCHEMA = "anomalica/transcript-cache/1"


def cache_path(store_dir: Path, hex_hash: str) -> Path:
    return store_dir / f"{hex_hash}.transcript.json"


def save_transcript_cache(
    path: Path,
    segments: list[Segment],
    speaker_segments: list[SpeakerSegment],
    meta: dict | None = None,
) -> None:
    payload = {
        "schema": CACHE_SCHEMA,
        "meta": meta or {},
        "segments": [asdict(s) for s in segments],
        "speaker_segments": [asdict(s) for s in speaker_segments],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def load_transcript_cache(
    path: Path,
) -> tuple[list[Segment], list[SpeakerSegment]]:
    data = json.loads(path.read_text())
    segments = [
        Segment(
            text=s["text"],
            start=s["start"],
            end=s["end"],
            words=[Word(**w) for w in s.get("words", [])],
        )
        for s in data["segments"]
    ]
    speaker_segments = [SpeakerSegment(**ss) for ss in data["speaker_segments"]]
    return segments, speaker_segments
