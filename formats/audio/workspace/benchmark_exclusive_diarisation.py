#!/usr/bin/env python3
"""Run and score one offline Community-1 diarisation pass per reviewed source."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from diarisation.pyannote_diarise import DIARISATION_MODEL, diarise
from evaluate_exclusive_diarisation import score_track_sets
from score_attribution import reviewed_words
from transcript_cache import load_raw_archive

SCHEMA = "anomalica/diarisation-benchmark/1"
DEFAULT_RECORDS = Path("/mnt/records")
DEFAULT_STORE = Path("/mnt/output/store")
DEFAULT_OUTPUT = Path("/mnt/vad/exclusive")
PREPROCESSING = "ffmpeg-pcm-s16le-mono-16000hz"


def _record_path(store: Path, source_hash: str) -> Path:
    v2 = store / f"{source_hash}.v2.md"
    return v2 if v2.exists() else store / f"{source_hash}.md"


def _write_result(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(path)


@contextmanager
def _prepared_audio(audio: Path):
    """Decode to Community-1's input shape before torchaudio loads the whole file."""
    with tempfile.TemporaryDirectory(prefix="community-1-") as directory:
        prepared = Path(directory) / "audio.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(audio),
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(prepared),
            ],
            check=True,
        )
        yield prepared


def benchmark_one(
    source_hash: str, records: Path, store: Path, output: Path
) -> dict[str, dict]:
    artifact = output / f"{source_hash}.community-1.json"
    if artifact.exists():
        payload = json.loads(artifact.read_text())
    else:
        audio = records / f"{source_hash}.opus"
        if not audio.exists():
            raise FileNotFoundError(audio)
        with _prepared_audio(audio) as prepared:
            _, pyannote = diarise(prepared)
        if not pyannote.get("exclusive_tracks"):
            raise RuntimeError("Community-1 returned no exclusive diarisation")
        payload = {
            "schema": SCHEMA,
            "source_hash": source_hash,
            "model": DIARISATION_MODEL,
            "audio_preprocessing": PREPROCESSING,
            "pyannote": pyannote,
        }
        _write_result(artifact, payload)

    archive = records / f"{source_hash}.transcript.json"
    record = _record_path(store, source_hash)
    if not archive.exists() or not record.exists():
        raise FileNotFoundError(archive if not archive.exists() else record)
    segments, _ = load_raw_archive(archive)
    truth = reviewed_words(record)
    return score_track_sets(segments, payload["pyannote"], truth)


def aggregate(results: dict[str, dict[str, dict]]) -> dict:
    totals = {}
    for strategy in ("regular", "exclusive"):
        matched = sum(result[strategy]["matched_words"] for result in results.values())
        wrong = sum(result[strategy]["wrong_words"] for result in results.values())
        turns = sum(result[strategy]["turns"] for result in results.values())
        wrong_turns = sum(
            result[strategy]["wrong_turns"] for result in results.values()
        )
        totals[strategy] = {
            "matched_words": matched,
            "wrong_words": wrong,
            "word_error_pct": round(100 * wrong / matched, 2),
            "turns": turns,
            "wrong_turns": wrong_turns,
            "turn_error_pct": round(100 * wrong_turns / turns, 2),
            "labels": sum(result[strategy]["labels"] for result in results.values()),
        }
    totals["adopt_exclusive"] = (
        totals["exclusive"]["wrong_words"] < totals["regular"]["wrong_words"]
        and totals["exclusive"]["wrong_turns"] <= totals["regular"]["wrong_turns"]
    )
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_hash", nargs="+")
    parser.add_argument("--records", type=Path, default=DEFAULT_RECORDS)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    results = {
        source_hash: benchmark_one(source_hash, args.records, args.store, args.output)
        for source_hash in args.source_hash
    }
    report = {"records": results, "aggregate": aggregate(results)}
    _write_result(args.output / "report.json", report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
