#!/usr/bin/env python3
"""Audit that every record's archived ORIGINAL is present - locally and (optionally)
in the Bunny storage zones. The blind-spot guard for source preservation.

Walks `ingests/store` RECURSIVELY (top level AND store/v1/) and reports the record
count it covered, so a future sweep that silently misses a third of the corpus -
as a non-recursive glob of store/*.md does - announces itself instead of reporting
success. A record is anything carrying a `content_hash`; intake stubs (no hash) key
to nothing and are counted separately, never as covered records.

Local check uses the workbench's own rule: the served file's stem is exactly the
hash (a `{hash}.transcript.json` sidecar does NOT count). The remote check lists
the Bunny zones (Bunny has no HEAD) and routes each object through the SHARED
`anomalica_common.publishing.zone_for` so open/gated cannot drift from the backfill.

Usage:
  audit_sources.py [--bunny]         # local audit; --bunny also checks the zones
Exit non-zero if any record's original is missing locally.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "anomalica"
if not (ROOT / "ingests").exists():  # fallback: sibling layout
    ROOT = Path("/home/mark/repos/anomalica")
STORE = ROOT / "ingests" / "store"
RECORDS = ROOT / "records"

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "anomalica-common" / "src")
)
sys.path.insert(0, "/home/mark/repos/anomalica/anomalica-common/src")
from anomalica_common.publishing import zone_for  # noqa: E402

MEDIA_EXT = (".opus", ".ogg", ".mp3", ".m4a", ".webm", ".mp4")
STORAGE_API = "https://storage.bunnycdn.com"
SOPS = os.path.expanduser("~/.nix-profile/bin/sops")
SECRETS = os.path.expanduser("~/repos/secrets/store/anomalica.yaml")
ZONE_ENV = {
    "anomalica-wb": "BUNNY_WB_STORAGE_PASSWORD",
    "anomalica-gated": "BUNNY_GATED_STORAGE_PASSWORD",
}


def _fm(text: str) -> str:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return m.group(1) if m else ""


def _field(fm: str, name: str) -> str | None:
    m = re.search(rf"^{name}:\s*(.+)$", fm, re.M)
    return m.group(1).strip().strip('"') if m else None


def _bare(v: str | None) -> str | None:
    return v.replace("sha256:", "").strip() if v else None


def _status(fm: str) -> str | None:
    m = re.search(r"^copyright:\s*\n\s*status:\s*(\S+)", fm, re.M)
    return m.group(1) if m else None


def _single_file_hash(fm: str) -> str | None:
    m = re.search(r"- role: single_file\s+hash: sha256:([0-9a-f]{64})", fm)
    return m.group(1) if m else None


def original_of(fm: str):
    """(hash, ext) of the record's archived original, matching operations'
    resolve_original: pdf/audio/video keyed by content_hash; ebook by
    content_hash|source_hash; web by its single_file snapshot, falling back to
    the raw fetch.

    The web fallback matters: a record ingested before frozen-page snapshots
    existed has no single_file entry, and keying it by one reports the original
    as missing when the raw fetch is sitting on disk under source_hash. Two
    records read as lost that way."""
    st = _field(fm, "source_type")
    ch = _bare(_field(fm, "content_hash"))
    sh = _bare(_field(fm, "source_hash"))
    if st in ("audio", "video"):
        return (ch, None)  # ext resolved from disk (any MEDIA_EXT)
    if st == "pdf":
        return (ch, "pdf")
    if st == "ebook":
        return (sh or ch, "epub")
    if st == "web":
        return (_single_file_hash(fm) or sh, "html")
    return (ch, _field(fm, "archived_ext"))


def local_file(hash_: str | None, ext: str | None) -> Path | None:
    """Present local original, workbench rule (stem == hash), or None."""
    if not hash_:
        return None
    if ext:
        p = RECORDS / f"{hash_}.{ext}"
        if p.exists():
            return p
    for cand in RECORDS.glob(f"{hash_}.*"):
        if cand.stem == hash_:  # exclude {hash}.transcript.json etc.
            return cand
    return None


def _sops(key: str) -> str | None:
    try:
        # Respect an explicitly-declared key file (the scheduler sets it); fall back
        # to the default path only when the caller has not. Declaration over default
        # so a HOME-less environment does not silently no-op every push.
        env = {**os.environ}
        env.setdefault(
            "SOPS_AGE_KEY_FILE", os.path.expanduser("~/.config/sops/age/keys.txt")
        )
        out = subprocess.run(
            [SOPS, "-d", "--extract", f'["{key}"]', SECRETS],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def _zone_keys(zone: str) -> set[str]:
    pw = _sops(ZONE_ENV[zone])
    if not pw:
        return set()
    req = urllib.request.Request(f"{STORAGE_API}/{zone}/sources/", method="GET")
    req.add_header("AccessKey", pw)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            items = json.loads(r.read().decode())
    except urllib.error.HTTPError:
        return set()
    return {"sources/" + i["ObjectName"] for i in items if not i.get("IsDirectory")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bunny", action="store_true", help="also check the Bunny zones")
    args = ap.parse_args()

    files = sorted(STORE.rglob("*.md"))
    records = 0
    stubs = 0
    local_missing = []
    for f in files:
        fm = _fm(f.read_text(errors="replace"))
        if not _field(fm, "content_hash"):
            stubs += 1  # intake stub: keys to nothing
            continue
        records += 1
        h, ext = original_of(fm)
        lf = local_file(h, ext)
        if lf is None:
            local_missing.append(
                (f.relative_to(STORE), _field(fm, "source_type"), _status(fm), h)
            )

    zone_keys = {}
    bunny_missing = []
    if args.bunny:
        for z in ZONE_ENV:
            zone_keys[z] = _zone_keys(z)
        for f in files:
            fm = _fm(f.read_text(errors="replace"))
            content_hash = _bare(_field(fm, "content_hash"))
            if not content_hash:
                continue
            h, ext = original_of(fm)
            lf = local_file(h, ext)
            if lf is None:
                continue
            # The remote key is content_hash + ext for EVERY type - the key the
            # workbench edge signs - even though the local FILE for web/ebook sits
            # under a different hash (single_file hash / source_hash). Keying the
            # remote lookup on the local hash false-alarms "not backed up" on
            # exactly web and ebook, in the dangerous direction.
            real_ext = lf.suffix.lstrip(".")
            key = f"sources/{content_hash}.{real_ext}"
            zone = zone_for(_status(fm), real_ext)
            if key not in zone_keys.get(zone, set()):
                bunny_missing.append((f.relative_to(STORE), zone, key))

    print(f"store files:        {len(files)}")
    print(f"records (content_hash): {records}")
    print(f"intake stubs (no hash): {stubs}")
    print(f"local original MISSING: {len(local_missing)}")
    for rel, st, status, h in local_missing:
        print(f"    {str(st):6} {str(status):20} {(h or '')[:12]}  {rel}")
    if args.bunny:
        print(
            f"bunny zone objects: {{{', '.join(f'{z}:{len(v)}' for z, v in zone_keys.items())}}}"
        )
        print(f"NOT on Bunny (has local, no remote): {len(bunny_missing)}")
        for rel, zone, key in bunny_missing[:40]:
            print(f"    {zone:16} {key}  {rel}")
    return 1 if local_missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
