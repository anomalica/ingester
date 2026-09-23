#!/usr/bin/env python3
"""Audit that every record's archived Assets are present locally and, optionally,
in the Bunny storage zones. The blind-spot guard for source preservation.

Walks the live `ingests/store` roots (top level and historical `store/v1/`) and
reports the record count it covered, while excluding retired
`store/legacy-identities/` audit history. A record is anything carrying a
`content_hash`; intake stubs (no hash) key to nothing and are counted separately.

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

import yaml

ROOT = Path(__file__).resolve().parents[2] / "anomalica"
if not (ROOT / "ingests").exists():  # fallback: sibling layout
    ROOT = Path("/home/mark/repos/anomalica")
STORE = ROOT / "ingests" / "store"
RECORDS = ROOT / "records"

sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "anomalica-common" / "src")
)
sys.path.insert(0, "/home/mark/repos/anomalica/product/anomalica-common/src")
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
    primary = _record3_primary(fm)
    if primary is not None:
        rights = primary.get("copyright")
        return rights.get("status") if isinstance(rights, dict) else None
    if _record3_assets(fm) is not None:
        return None
    m = re.search(r"^copyright:\s*\n\s*status:\s*(\S+)", fm, re.M)
    return m.group(1) if m else None


def _single_file_hash(fm: str) -> str | None:
    m = re.search(r"- role: single_file\s+hash: sha256:([0-9a-f]{64})", fm)
    return m.group(1) if m else None


def _record3_assets(fm: str) -> list[dict] | None:
    try:
        value = yaml.safe_load(fm)
    except yaml.YAMLError:
        return None
    if not isinstance(value, dict) or value.get("schema") != "anomalica/record/3":
        return None
    assets = value.get("assets")
    if not isinstance(assets, list) or any(
        not isinstance(asset, dict) for asset in assets
    ):
        return []
    return assets


def _record3_primary(fm: str) -> dict | None:
    assets = _record3_assets(fm)
    return assets[0] if assets is not None and len(assets) == 1 else None


def original_of(fm: str):
    """Return the one archived original usable by the legacy storage pointer.

    A composite record/3 deliberately has no singular original or synthetic
    Record-hash object, so this returns ``(None, None)`` for composites. The web
    fallback matters for legacy records: pre-snapshot records retain only the raw
    fetch under ``source_hash``.
    """
    primary = _record3_primary(fm)
    if primary is not None:
        return (
            _bare(primary.get("asset_hash")),
            primary.get("archived_ext"),
        )
    if _record3_assets(fm) is not None:
        return (None, None)
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


def storage_key(fm: str, real_ext: str) -> str | None:
    """Canonical remote key: Asset identity for /3, legacy Record key otherwise."""
    primary = _record3_primary(fm)
    if _record3_assets(fm) is not None and primary is None:
        return None
    identity = (
        _bare(primary.get("asset_hash"))
        if primary is not None
        else _bare(_field(fm, "content_hash"))
    )
    return f"sources/{identity}.{real_ext}" if identity else None


def archived_storage_key(fm: str, asset_hash: str | None, real_ext: str) -> str | None:
    """Remote key for one resolved archive binding across schema generations."""
    if _record3_assets(fm) is not None:
        return f"sources/{asset_hash}.{real_ext}" if asset_hash else None
    return storage_key(fm, real_ext)


def archived_assets(
    fm: str,
) -> list[tuple[str | None, str | None, str | None, str | None]]:
    """Every authoritative archive binding as ``(hash, ext, rights, type)``."""
    assets = _record3_assets(fm)
    if assets is not None:
        if not assets:
            return [(None, None, None, None)]
        result = []
        for asset in assets:
            rights = asset.get("copyright")
            result.append(
                (
                    _bare(asset.get("asset_hash")),
                    asset.get("archived_ext"),
                    rights.get("status") if isinstance(rights, dict) else None,
                    asset.get("source_type"),
                )
            )
        try:
            frontmatter = yaml.safe_load(fm)
        except yaml.YAMLError:
            frontmatter = {}
        snapshots = (
            frontmatter.get("snapshots") if isinstance(frontmatter, dict) else None
        )
        if snapshots is not None:
            if not isinstance(snapshots, list):
                result.append((None, None, None, None))
            else:
                for snapshot in snapshots:
                    derivative = (
                        snapshot.get("asset") if isinstance(snapshot, dict) else None
                    )
                    if not isinstance(derivative, dict):
                        result.append((None, None, None, None))
                        continue
                    rights = derivative.get("copyright")
                    result.append(
                        (
                            _bare(derivative.get("asset_hash")),
                            derivative.get("archived_ext"),
                            rights.get("status") if isinstance(rights, dict) else None,
                            derivative.get("source_type"),
                        )
                    )
        return result
    hash_, ext = original_of(fm)
    return [(hash_, ext, _status(fm), _field(fm, "source_type"))]


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

    files = sorted(
        path
        for path in STORE.rglob("*.md")
        if "legacy-identities" not in path.relative_to(STORE).parts
    )
    records = 0
    stubs = 0
    local_missing = []
    for f in files:
        fm = _fm(f.read_text(errors="replace"))
        if not _field(fm, "content_hash"):
            stubs += 1  # intake stub: keys to nothing
            continue
        records += 1
        for h, ext, status, source_type in archived_assets(fm):
            lf = local_file(h, ext)
            if lf is None:
                local_missing.append((f.relative_to(STORE), source_type, status, h))

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
            for h, ext, status, _ in archived_assets(fm):
                lf = local_file(h, ext)
                if lf is None or not h:
                    continue
                real_ext = lf.suffix.lstrip(".")
                key = archived_storage_key(fm, h, real_ext)
                if key is None:
                    continue
                zone = zone_for(status, real_ext)
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
