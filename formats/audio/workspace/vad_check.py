#!/usr/bin/env python3
"""Measure what a voice-activity setting hears in a recording, and what it misses.

Quiet speech is the failure mode that matters: an interviewer's question a few
decibels below the guest's voice is either dropped or replaced with invented
words, and both are invisible in the output. The measure of a VAD setting is
therefore not its word count but how much speech the diariser hears while
transcription produces nothing - a gap with a speaker in it.

Diarisation is read from the record's cached transcript archive rather than run
again: it does not depend on the VAD settings under test, and it is the
expensive half.

    cm run vad-check <record-hash> <start> <length> <onset> <offset>

Run it through the `vad-check` command, not a bare `cm run python`: the
model caches and the GPU batch size are set per command, so a bare run
re-downloads the alignment model and transcribes at a batch the card
cannot always hold.

A length of 0 takes the whole recording.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, "/mnt/shared")

RECORDS = Path("/mnt/records")

#: A silence at least this long, with a diarised speaker inside it for at least
#: MIN_SPEECH_SECONDS, is speech the transcription did not hear.
MIN_GAP_SECONDS = 3.0
MIN_SPEECH_SECONDS = 1.5


def source(stem: str) -> Path:
    for suffix in (".opus", ".ogg", ".m4a", ".mp3", ".wav", ".mp4", ".webm"):
        path = RECORDS / f"{stem}{suffix}"
        if path.exists():
            return path
    raise SystemExit(f"no archived audio for {stem[:12]}")


def window(stem: str, start: float, length: float) -> Path:
    out = Path(f"/tmp/{stem[:12]}-{int(start)}-{int(length)}.wav")
    if out.exists():
        return out
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-ss", str(start)]
    if length > 0:
        cmd += ["-t", str(length)]
    cmd += ["-i", str(source(stem)), "-ac", "1", "-ar", "16000", str(out)]
    subprocess.run(cmd, check=True)
    return out


def transcribe_with(audio: Path, onset: float, offset: float) -> list[dict]:
    os.environ["INGEST_VAD_ONSET"] = str(onset)
    os.environ["INGEST_VAD_OFFSET"] = str(offset)
    from transcription.whisperx_transcribe import transcribe

    segments, _raw = transcribe(audio)
    return [{"start": s.start, "end": s.end, "text": s.text} for s in segments]


def cached(stem: str) -> tuple[list[dict], list[dict]]:
    data = json.loads((RECORDS / f"{stem}.transcript.json").read_text())
    segments = [
        {"start": s["start"], "end": s["end"], "text": s["text"]}
        for s in data["whisperx"]["aligned"]["segments"]
    ]
    return segments, data.get("pyannote", {}).get("tracks", [])


def unheard(
    segments: list[dict], tracks: list[dict], offset: float = 0.0
) -> tuple[int, float]:
    """Gaps between transcribed segments that a diarised speaker sits inside."""
    count = seconds = 0.0
    for a, b in zip(segments, segments[1:]):
        lo, hi = a["end"] + offset, b["start"] + offset
        if hi - lo < MIN_GAP_SECONDS:
            continue
        speech = sum(max(0.0, min(t["end"], hi) - max(t["start"], lo)) for t in tracks)
        if speech >= MIN_SPEECH_SECONDS:
            count += 1
            seconds += speech
    return int(count), seconds


def report(label: str, segments: list[dict], tracks: list[dict], offset: float) -> None:
    words = sum(len(s["text"].split()) for s in segments)
    voiced = sum(s["end"] - s["start"] for s in segments)
    gaps, unheard_seconds = unheard(segments, tracks, offset)
    print(
        f"{label:<22} {len(segments):>5} segments {words:>6} words "
        f"{voiced:8.1f}s voiced  {gaps:>3} gaps with a speaker in them "
        f"({unheard_seconds:.1f}s)",
        flush=True,
    )


def main(argv: list[str]) -> int:
    stem, start, length, onset, offset = (
        argv[0],
        float(argv[1]),
        float(argv[2]),
        float(argv[3]),
        float(argv[4]),
    )
    audio = window(stem, start, length)
    stored, tracks = cached(stem)
    if length > 0:
        stored = [s for s in stored if s["end"] > start and s["start"] < start + length]
    report("stored", stored, tracks, 0.0)
    fresh = transcribe_with(audio, onset, offset)
    report(f"onset {onset} offset {offset}", fresh, tracks, start)
    out = Path(f"/tmp/{stem[:12]}.vad-{onset}-{offset}.json")
    out.write_text(json.dumps(fresh, indent=1))
    print("wrote", out, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
