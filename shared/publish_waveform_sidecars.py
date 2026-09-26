#!/usr/bin/env python3
"""Reconcile waveform sidecars with the storage zones under Asset rights.

The local archive is authoritative. Peaks use their own disclosure rule, which
can differ from the original audio's; neither a Record hash nor the audio's zone
is a safe substitute for the Asset's hash and the peaks routing rule.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from audit_sources import (
    RECORDS,
    STORE,
    ZONE_ENV,
    _fm,
    _sops,
    _zone_keys,
    archived_assets,
)
from bunny_storage import _put

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "anomalica-common" / "src")
)
from anomalica_common.publishing import OPEN_ZONE, zone_for  # noqa: E402


def planned(records: list[Path]) -> dict[str, str]:
    result = {}
    for record in records:
        for hash_, _, rights, source_type in archived_assets(
            _fm(record.read_text(errors="replace"))
        ):
            if source_type not in {"audio", "video"} or not hash_:
                continue
            if not (RECORDS / f"{hash_}.peaks.json").is_file():
                continue
            zone = zone_for(rights, "peaks.json")
            if hash_ in result and result[hash_] != zone:
                raise ValueError(f"conflicting rights for waveform Asset {hash_}")
            result[hash_] = zone
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, help="publish for one new ingest")
    parser.add_argument("--write", action="store_true", help="upload missing waveforms")
    args = parser.parse_args()
    records = (
        [args.record]
        if args.record
        else [
            p
            for p in STORE.rglob("*.md")
            if "legacy-identities" not in p.relative_to(STORE).parts
        ]
    )
    wanted = planned(records)
    existing = {zone: _zone_keys(zone) for zone in ZONE_ENV}
    if not all(existing.values()):
        print("Unable to list both storage zones", file=sys.stderr)
        return 1
    if not args.record:
        unexpected_open = sorted(
            key
            for key in existing[OPEN_ZONE]
            if key.endswith(".peaks.json")
            and wanted.get(key.removeprefix("sources/").removesuffix(".peaks.json"))
            != OPEN_ZONE
        )
        if unexpected_open:
            for key in unexpected_open:
                print(f"UNAUTHORISED OPEN PEAKS {key}", file=sys.stderr)
            return 1
    missing = []
    misplaced = []
    for hash_, zone in sorted(wanted.items()):
        key = f"sources/{hash_}.peaks.json"
        if key in existing[zone]:
            continue
        if any(key in keys for other, keys in existing.items() if other != zone):
            misplaced.append((hash_, zone))
        else:
            missing.append((hash_, zone))
    print(
        f"Waveforms: {len(wanted)} local, {len(missing)} remote missing, {len(misplaced)} in wrong zone"
    )
    for hash_, zone in misplaced:
        print(f"WRONG ZONE {hash_} expected {zone}", file=sys.stderr)
    if not args.write or misplaced:
        return 1 if misplaced else 0

    credentials = {
        zone: _sops(key)
        for zone, key in ZONE_ENV.items()
        if any(z == zone for _, z in missing)
    }
    if not all(credentials.values()):
        print("Unable to read storage credentials", file=sys.stderr)
        return 1
    failures = 0
    for hash_, zone in missing:
        key = f"sources/{hash_}.peaks.json"
        status = _put(
            zone,
            key,
            credentials[zone],
            (RECORDS / f"{hash_}.peaks.json").read_bytes(),
            "peaks.json",
        )
        if status not in (200, 201):
            print(f"UPLOAD FAILED {hash_} ({status})", file=sys.stderr)
            failures += 1
    print(f"Uploaded {len(missing) - failures} waveforms; failed {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
