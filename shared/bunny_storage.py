#!/usr/bin/env python3
"""Push a record's archived ORIGINAL to the Bunny storage zone at archive time, and
stamp a `storage:` pointer so consumers never guess where it lives.

The write side of the sources zone (scheduler's push-at-archive contract). Routing
is imported from `anomalica_common.publishing.zone_for` - never re-derived - so the
open/gated decision cannot drift from operations' backfill. The per-type "which file
is the original" resolution is shared with the audit (`audit_sources.original_of`)
for the same reason. Object key is `sources/{hash}.{ext}` - the key the workbench
edge signs.

FAIL CLOSED: unknown/missing status routes GATED (via zone_for). NON-FATAL: a push
failure never fails the ingest - the local archive is the source of truth; the
record is left without a `storage:` block so the audit/reconcile retries it.
Idempotent: LIST the zone (Bunny has no HEAD) and skip if the key is present.

Usage: bunny_storage.py <record.md> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_sources import (  # noqa: E402  reuse resolution - no drift
    STORAGE_API,
    ZONE_ENV,
    _fm,
    _sops,
    _status,
    local_file,
    original_of,
)

sys.path.insert(0, "/home/mark/repos/anomalica/anomalica-common/src")
from anomalica_common.publishing import OPEN_ZONE, zone_for  # noqa: E402

MIME = {
    "pdf": "application/pdf",
    "epub": "application/epub+zip",
    "html": "text/html",
    "opus": "audio/opus",
    "ogg": "audio/ogg",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "mp4": "video/mp4",
    "webm": "video/webm",
}


def _zone_has(zone: str, key: str, pw: str) -> bool:
    req = urllib.request.Request(f"{STORAGE_API}/{zone}/sources/", method="GET")
    req.add_header("AccessKey", pw)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            items = json.loads(r.read().decode())
    except urllib.error.HTTPError:
        return False
    return any(
        ("sources/" + i["ObjectName"]) == key for i in items if not i.get("IsDirectory")
    )


def _put(zone: str, key: str, pw: str, data: bytes, ext: str) -> int:
    req = urllib.request.Request(f"{STORAGE_API}/{zone}/{key}", data=data, method="PUT")
    req.add_header("AccessKey", pw)
    req.add_header("Content-Type", MIME.get(ext, "application/octet-stream"))
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def _stamp(md: Path, zone: str, key: str) -> None:
    """Write a `storage:` block onto the record (idempotent, hashless field)."""
    text = md.read_text(errors="replace")
    m = re.match(r"^(---\n)(.*?)(\n---\n)", text, re.S)
    if not m:
        return
    fmv = m.group(2)
    block = (
        "storage:\n"
        f"  zone_class: {'open' if zone == OPEN_ZONE else 'gated'}\n"
        f"  key: {key}\n"
        f"  pushed_at: {datetime.now(timezone.utc).isoformat()}"
    )
    if re.search(r"^storage:\s*$", fmv, re.M):
        fmv = re.sub(
            r"^storage:\n(?:[ \t]+.*\n?)*", block + "\n", fmv, count=1, flags=re.M
        ).rstrip("\n")
    else:
        fmv = fmv.rstrip("\n") + "\n" + block
    md.write_text(m.group(1) + fmv + m.group(3) + text[m.end() :])


def push_record(md: Path, dry_run: bool = False) -> str:
    """Push the record's original if not already on Bunny; stamp `storage:`.
    Returns one of: pushed | exists | no-original | no-cred | failed."""
    fm = _fm(md.read_text(errors="replace"))
    h, ext = original_of(fm)
    lf = local_file(h, ext)
    if lf is None:
        return "no-original"
    real_ext = lf.suffix.lstrip(".")
    key = f"sources/{h}.{real_ext}"
    zone = zone_for(_status(fm), real_ext)
    pw = _sops(ZONE_ENV[zone])
    if not pw:
        return "no-cred"
    if dry_run:
        return "would-push"
    if _zone_has(zone, key, pw):
        _stamp(md, zone, key)
        return "exists"
    code = _put(zone, key, pw, lf.read_bytes(), real_ext)
    if code in (200, 201):
        _stamp(md, zone, key)
        return "pushed"
    return f"failed({code})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("record", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.record.exists():
        print(f"no such record: {args.record}", file=sys.stderr)
        return 1
    result = push_record(args.record, dry_run=args.dry_run)
    print(f"{result}  {args.record.name}")
    return 0 if result in ("pushed", "exists", "would-push", "no-original") else 1


if __name__ == "__main__":
    raise SystemExit(main())
