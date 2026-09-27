#!/usr/bin/env python3
"""Audit and backfill the waveform sidecars of archived audio/video Assets.

Reads live ingest envelopes (including historical v1), never retranscribes or
changes a Record. The default is a dry audit; --write decodes only missing Assets.
"""

from __future__ import annotations

import argparse
import base64
import json
import mmap
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from audit_sources import _fm, archived_assets

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "anomalica-common" / "src")
)
from anomalica_common.peaks import (  # noqa: E402
    BINS_PER_SECOND,
    DECODE_RATE,
    PEAKS_SCHEMA,
    bins_for,
    peaks_payload,
)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STORE = ROOT / "product" / "ingests" / "store"
DEFAULT_RECORDS = ROOT / "records"
AUDIO_EXTENSIONS = {"opus", "ogg", "mp3", "m4a", "wav", "flac", "webm", "mp4"}


def bindings(
    store: Path, records: Path
) -> dict[str, tuple[Path | None, str, list[str]]]:
    """Distinct live Assets, with record paths for a reproducible audit."""
    found: dict[str, tuple[Path | None, str, list[str]]] = {}
    for record in sorted(store.rglob("*.md")):
        if "legacy-identities" in record.relative_to(store).parts:
            continue
        fm = _fm(record.read_text(errors="replace"))
        # Historical replacements retain their envelope and may document a
        # genuinely lost original. Audit waveform coverage over live Records;
        # do not count a retired missing Asset as a broken current waveform.
        if re.search(r"^superseded_by:\s*\S+", fm, re.MULTILINE):
            continue
        for hash_, ext, _, source_type in archived_assets(fm):
            if source_type not in {"audio", "video"} or not hash_:
                continue
            # /3 has an explicit archived extension; legacy records may not.
            if ext:
                candidate = records / f"{hash_}.{ext}"
                audio = candidate if candidate.is_file() else None
            else:
                matches = [
                    p
                    for suffix in sorted(AUDIO_EXTENSIONS)
                    if (p := records / f"{hash_}.{suffix}").is_file()
                ]
                audio = matches[0] if len(matches) == 1 else None
            if hash_ in found:
                previous, previous_type, paths = found[hash_]
                if previous != audio:
                    raise ValueError(f"conflicting archive bindings for {hash_}")
                paths.append(str(record.relative_to(store)))
                found[hash_] = (audio, previous_type, paths)
            else:
                found[hash_] = (audio, source_type, [str(record.relative_to(store))])
    return found


def valid_sidecar(path: Path, hash_: str) -> bool:
    try:
        payload = json.loads(path.read_text())
        decoded = base64.b64decode(payload["peaks"], validate=True)
        duration = payload["duration"]
        return (
            payload["schema"] == PEAKS_SCHEMA
            and payload["hex_hash"] == hash_
            and payload["bins_per_second"] == BINS_PER_SECOND
            and type(duration) in (int, float)
            and duration > 0
            and len(decoded) == bins_for(duration)
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def generate(audio: Path, target: Path, hash_: str) -> None:
    """Decode to a temporary PCM file, reduce via the common peak algorithm."""
    with tempfile.TemporaryDirectory(prefix="waveform-") as directory:
        pcm_path = Path(directory) / "audio.pcm"
        with pcm_path.open("wb") as output:
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-i",
                    str(audio),
                    "-map",
                    "0:a:0",
                    "-ac",
                    "1",
                    "-ar",
                    str(DECODE_RATE),
                    "-f",
                    "s16le",
                    "-acodec",
                    "pcm_s16le",
                    "-",
                ],
                stdout=output,
                check=True,
            )
        if not pcm_path.stat().st_size:
            raise ValueError(f"no decoded audio in {audio}")
        with (
            pcm_path.open("rb") as input_file,
            mmap.mmap(input_file.fileno(), 0, access=mmap.ACCESS_READ) as pcm,
        ):
            payload = peaks_payload(pcm, hash_)
        # A sidecar is derived but a reader must never see a partially written one.
        with tempfile.NamedTemporaryFile(
            mode="w", dir=target.parent, prefix=f".{target.name}.", delete=False
        ) as output:
            temporary = Path(output.name)
            json.dump(payload, output, separators=(",", ":"))
            output.write("\n")
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE)
    parser.add_argument("--records", type=Path, default=DEFAULT_RECORDS)
    parser.add_argument(
        "--write", action="store_true", help="generate missing sidecars"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="show only failures and totals"
    )
    parser.add_argument(
        "--audio", type=Path, help="one newly archived audio/video Asset"
    )
    parser.add_argument("--hash", dest="asset_hash", help="SHA-256 of --audio Asset")
    args = parser.parse_args()
    if args.audio or args.asset_hash:
        if not args.audio or not args.asset_hash or not args.write:
            parser.error("--audio requires --hash and --write")
        if (
            args.audio.stem != args.asset_hash
            or args.audio.suffix.lstrip(".") not in AUDIO_EXTENSIONS
        ):
            parser.error("audio filename must be the content-addressed audio Asset")
        sidecar = args.records / f"{args.asset_hash}.peaks.json"
        if sidecar.exists():
            if not valid_sidecar(sidecar, args.asset_hash):
                parser.error(f"existing peaks sidecar is invalid: {sidecar}")
            return 0
        generate(args.audio, sidecar, args.asset_hash)
        return 0 if valid_sidecar(sidecar, args.asset_hash) else 1
    if not args.store.is_dir() or not args.records.is_dir():
        parser.error("store and records directories must exist")

    assets = bindings(args.store, args.records)
    counts: Counter[str] = Counter()
    failures = 0
    for hash_, (audio, source_type, paths) in sorted(assets.items()):
        sidecar = args.records / f"{hash_}.peaks.json"
        if audio is None:
            counts[f"{source_type}:original_missing"] += 1
            print(f"ORIGINAL MISSING {hash_} {', '.join(paths)}")
            failures += 1
        elif sidecar.exists() and valid_sidecar(sidecar, hash_):
            counts[f"{source_type}:present"] += 1
        elif sidecar.exists():
            counts[f"{source_type}:invalid"] += 1
            print(f"INVALID {hash_} {', '.join(paths)}")
            failures += (
                1  # Never overwrite an existing sidecar without investigating it.
            )
        else:
            counts[f"{source_type}:missing"] += 1
            if not args.quiet:
                print(f"MISSING {hash_} {', '.join(paths)}")
            if args.write:
                try:
                    generate(audio, sidecar, hash_)
                    if not valid_sidecar(sidecar, hash_):
                        raise ValueError("generated sidecar did not validate")
                    counts[f"{source_type}:written"] += 1
                except (OSError, ValueError, subprocess.CalledProcessError) as exc:
                    print(f"FAILED {hash_}: {exc}", file=sys.stderr)
                    failures += 1
    print(
        f"Assets: {len(assets)}; "
        + ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
