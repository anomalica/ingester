#!/usr/bin/env python3
"""Supersede a legacy audio/video Record whose exact archived audio was lost.

The replacement Asset has its own hash and therefore a different Record/3
identity. Keep the old envelope and its sidecars as history; never pretend a
fresh remux has the missing Asset's byte identity. No provider is called here.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import re
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from dates import normalise_published
from record3 import (
    Record3Error,
    _assert_no_selection_collapse,
    _atomic_write,
    _record_text,
    build_default_record3,
    read_record,
)
from validator import validate


def prepare(
    old_path: Path, asset_path: Path, ingests: Path
) -> tuple[Path, str, str, list[Path], list[Path]]:
    """Validate both identities, return the prepared replacement and old paths."""
    store = ingests / "store"
    if old_path.parent != store or not old_path.name.endswith(".v2.md"):
        raise Record3Error("recovery requires a live legacy word-timed Record")
    old = read_record(old_path)
    fm = old.frontmatter
    if fm.get("schema") != "anomalica/record/2" or fm.get("source_type") not in {
        "audio",
        "video",
    }:
        raise Record3Error("recovery requires an audio/video record/2")
    old_hash = fm.get("content_hash")
    if old_hash != f"sha256:{old_path.name.removesuffix('.v2.md')}":
        raise Record3Error("legacy Record identity does not match its path")
    if fm.get("source_hash") or fm.get("superseded_by") or fm.get("retired_into"):
        raise Record3Error("legacy Record has a different Asset binding or is retired")
    if any(
        (asset_path.parent / f"{old_path.name.removesuffix('.v2.md')}.{ext}").exists()
        for ext in ("opus", "ogg", "mp3", "m4a", "webm", "wav")
    ):
        raise Record3Error("original Asset is present; use ordinary migration")
    if not asset_path.is_file() or asset_path.suffix != ".opus":
        raise Record3Error("recovered Opus Asset is missing")
    new_asset_hash = hashlib.sha256(asset_path.read_bytes()).hexdigest()
    if (
        asset_path.name != f"{new_asset_hash}.opus"
        or new_asset_hash == old_path.name.removesuffix(".v2.md")
    ):
        raise Record3Error("recovered audio needs its own content-addressed Asset")
    if (fm.get("copyright") or {}).get("status") is None or not fm.get("source_url"):
        raise Record3Error("replacement lacks source or copyright authority")
    if (store / f"{old_hash.removeprefix('sha256:')}.review.json").exists():
        raise Record3Error("human review must be carried explicitly before replacement")
    _assert_no_selection_collapse(store, old_path, f"sha256:{new_asset_hash}")

    acquired = (
        datetime.fromtimestamp(asset_path.stat().st_mtime, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    revised = deepcopy(fm)
    revised.pop("date_accessed", None)  # This was the lost Asset's retrieval instant.
    processing = revised.get("processing")
    if not isinstance(processing, dict):
        raise Record3Error("legacy extraction provenance is missing")
    source = processing.get("source")
    tracks = source.get("audio") if isinstance(source, dict) else None
    if (
        not isinstance(tracks, list)
        or len(tracks) != 1
        or tracks[0].get("sha256") != old_hash.removeprefix("sha256:")
    ):
        raise Record3Error("legacy extraction source does not name the lost Asset")
    if tracks[0].get("size_bytes") != asset_path.stat().st_size:
        raise Record3Error(
            "reacquired audio length differs from the retained extraction"
        )
    if fm.get("duration") is not None:
        probed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nokey=1:noprint_wrappers=1",
                str(asset_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        if abs(float(probed.stdout.strip()) - float(fm["duration"])) > 0.03:
            raise Record3Error(
                "reacquired audio duration differs from the retained transcript"
            )
    tracks[0]["sha256"] = new_asset_hash
    tracks[0]["size_bytes"] = asset_path.stat().st_size
    tracks[0]["fetched_at"] = acquired
    processing["transcript_reused_from_asset"] = old_hash
    processing["transcript_reuse_basis"] = (
        "same source URL and duration; local speech comparison at start, middle and end"
    )
    new_fm, _ = build_default_record3(
        revised,
        old.body,
        asset_path,
        {
            "fetched_at": acquired,
            "fetched_url": fm["source_url"],
            "source_id": fm.get("source_id"),
        },
    )
    new_hash = new_fm["content_hash"].removeprefix("sha256:")
    new_path = store / f"{new_hash}.md"
    if new_path.exists():
        raise Record3Error("replacement Record already exists")
    new_fm["supersedes"] = old_hash.removeprefix("sha256:")
    if new_fm.get("date_extracted") is not None:
        new_fm["date_extracted"] = normalise_published(
            new_fm["date_extracted"]
        ).replace("+00:00", "Z")
    provenance = new_fm.get("provenance")
    if isinstance(provenance, dict):
        for field in ("published_date", "posted_date", "updated_date"):
            if provenance.get(field) is not None:
                provenance[field] = normalise_published(provenance[field])
    content = _record_text(new_fm, old.body)
    result = validate(content, expected_schema="anomalica/record/3")
    if result.errors:
        raise Record3Error("replacement format is invalid: " + "; ".join(result.errors))
    sidecars = sorted(store.glob(f"{old_path.name.removesuffix('.v2.md')}.*"))
    if any((store / "v1" / path.name).exists() for path in sidecars):
        raise Record3Error("retirement destination already exists")
    aliases = [
        p
        for p in (ingests / "by-name").iterdir()
        if p.is_symlink() and p.resolve() == old_path
    ]
    if not aliases:
        raise Record3Error("legacy Record has no human alias")
    if any(p.with_name(f".{p.name}.recovery").exists() for p in aliases):
        raise Record3Error("alias scratch path occupied")
    retired = old.raw.replace(
        f"content_hash: {old_hash}\n",
        f"content_hash: {old_hash}\nsuperseded_by: {new_hash}\n"
        'superseded_reason: "Exact archived audio was lost; reacquired audio has a new Asset identity."\n',
        1,
    )
    if retired == old.raw:
        raise Record3Error("cannot stamp old Record identity")
    return new_path, content, retired, sidecars, aliases


def recover(old_path: Path, asset_path: Path, ingests: Path) -> Path:
    new_path, content, retired, sidecars, aliases = prepare(
        old_path, asset_path, ingests
    )
    store = ingests / "store"
    (store / "v1").mkdir(exist_ok=True)
    _atomic_write(new_path, content)
    _atomic_write(old_path, retired)
    for path in sidecars:
        path.replace(store / "v1" / path.name)
    for alias in aliases:
        scratch = alias.with_name(f".{alias.name}.recovery")
        if scratch.exists() or scratch.is_symlink():
            raise Record3Error("alias scratch path occupied")
        scratch.symlink_to(os.path.relpath(new_path, alias.parent))
        scratch.replace(alias)
    return new_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ingests-dir", type=Path, required=True)
    parser.add_argument("--records-dir", type=Path, required=True)
    parser.add_argument(
        "--pair", action="append", required=True, help="old Record hash:new Asset hash"
    )
    args = parser.parse_args()
    ingests = args.ingests_dir.resolve()
    records = args.records_dir.resolve()
    if subprocess.run(
        ["git", "-C", str(ingests), "status", "--porcelain"],
        capture_output=True,
        check=True,
    ).stdout:
        parser.error("ingests worktree must be clean")
    git_dir = subprocess.run(
        ["git", "-C", str(ingests), "rev-parse", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    common = Path(git_dir)
    if not common.is_absolute():
        common = (ingests / common).resolve()
    with (common / "anomalica-write.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if subprocess.run(
            ["git", "-C", str(ingests), "status", "--porcelain"],
            capture_output=True,
            check=True,
        ).stdout:
            parser.error("ingests worktree changed while taking write lock")
        pairs = []
        for raw in args.pair:
            if not re.fullmatch(r"[0-9a-f]{64}:[0-9a-f]{64}", raw):
                parser.error("pair requires old and new full lowercase SHA-256 hashes")
            old, new = raw.split(":")
            source = ingests / "store" / f"{old}.v2.md"
            asset = records / f"{new}.opus"
            preview = prepare(source, asset, ingests)
            pairs.append((source, asset, preview))
        paths = []
        for source, asset, (_, _, _, sidecars, aliases) in pairs:
            old_stem = source.name.removesuffix(".v2.md")
            new_path = recover(source, asset, ingests)
            paths.append(f"store/{new_path.name}")
            paths.extend(f"store/{path.name}" for path in sidecars)
            paths.extend(f"store/v1/{path.name}" for path in sidecars)
            paths.extend(f"by-name/{p.name}" for p in aliases)
            print(f"{old_stem} -> {new_path.stem}")
        subprocess.run(
            ["git", "-C", str(ingests), "add", "-A", "--", *sorted(set(paths))],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(ingests),
                "commit",
                "-m",
                "fix(records): replace three lost audio Assets",
            ],
            check=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
