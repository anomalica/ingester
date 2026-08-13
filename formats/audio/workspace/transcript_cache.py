"""Durable archive of the COMPLETE raw transcription + diarisation output.

Written beside the source audio at sources/{hash}.transcript.json - NOT a
gitignored cache. The raw AI output is closer to source than the processed
record, so it is preserved verbatim as provenance and never discarded: the
full whisperx result (per-word + segment confidences, no_speech/avg_logprob,
detected language) and the pyannote diarisation tracks. It also lets a record
be re-rendered into any future format with no GPU re-run - load the archive,
reconstruct the processed segments, re-align/re-render.

Distinct from the derived words.json sidecar (a flattened subset for
consumers); this is the lot.
"""

from __future__ import annotations

import json
from pathlib import Path

from models import Segment, SpeakerSegment, Word

ARCHIVE_SCHEMA = "anomalica/transcript-archive/1"


def _json_default(o):
    """Coerce non-JSON-serialisable values (numpy float32/int from whisperx,
    numpy arrays) to plain Python so the raw output serialises losslessly."""
    if hasattr(o, "item"):  # numpy scalar
        return o.item()
    if hasattr(o, "tolist"):  # numpy array
        return o.tolist()
    return str(o)


def archive_path(records_dir: Path, hex_hash: str) -> Path:
    return records_dir / f"{hex_hash}.transcript.json"


def save_raw_archive(
    path: Path,
    whisperx_raw: dict,
    pyannote_raw: dict,
    meta: dict | None = None,
) -> None:
    """Write the complete raw transcription + diarisation output verbatim."""
    payload = {
        "schema": ARCHIVE_SCHEMA,
        "meta": meta or {},
        "whisperx": whisperx_raw,
        "pyannote": pyannote_raw,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, default=_json_default))


def load_raw_archive(path: Path) -> tuple[list[Segment], list[SpeakerSegment]]:
    """Reconstruct the processed (segments, speaker_segments) from the archive,
    so a re-render skips the GPU entirely. Mirrors what transcribe()+diarise()
    return from their raw output."""
    data = json.loads(path.read_text())

    aligned = data["whisperx"]["aligned"]["segments"]
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
        for seg in aligned
    ]

    speaker_segments = [
        SpeakerSegment(speaker=t["speaker"], start=t["start"], end=t["end"])
        for t in data["pyannote"].get("tracks", [])
    ]
    return segments, speaker_segments
