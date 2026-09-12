#!/usr/bin/env python3
"""Resolve and publish the post-commit ingest result contract."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import yaml


SCHEMA = "anomalica/ingest-result/1"
PREFIX = "ANOMALICA_INGEST_RESULT "
HASH_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
RECORD_SCHEMAS = {"anomalica/record/1", "anomalica/record/2"}


class ResultError(RuntimeError):
    pass


def _git(repo: Path, *args: str, text: bool = True) -> str | bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=text
    )
    if proc.returncode != 0:
        detail = (
            proc.stderr.strip()
            if text
            else proc.stderr.decode(errors="replace").strip()
        )
        raise ResultError(detail or f"git {' '.join(args)} failed")
    return proc.stdout


def _frontmatter(data: bytes) -> dict:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ResultError("record is not valid UTF-8") from exc
    match = re.match(r"^---\n(.*?)\n---(?:\n|$)", text, re.DOTALL)
    if not match:
        raise ResultError("record has no YAML frontmatter")
    try:
        value = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise ResultError(f"record frontmatter is invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise ResultError("record frontmatter is not a mapping")
    return value


def _canonical_uuid(value: str) -> str:
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise ResultError("run_uuid must be a canonical lowercase UUID") from exc
    canonical = str(parsed)
    if value != canonical:
        raise ResultError("run_uuid must be a canonical lowercase UUID")
    return canonical


def _relative_record_path(ingests_dir: Path, value: str) -> str:
    repo = ingests_dir.resolve()
    supplied = Path(value)
    if supplied.is_absolute():
        try:
            value = supplied.resolve().relative_to(repo).as_posix()
        except ValueError as exc:
            raise ResultError("record_path is outside the ingests repository") from exc
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(p in {"", ".", ".."} for p in path.parts)
    ):
        raise ResultError("record_path must be a normalised relative POSIX path")
    normalised = path.as_posix()
    if normalised != value:
        raise ResultError("record_path must be a normalised relative POSIX path")
    return normalised


def _record_values(path: Path) -> tuple[str, str | None, str | None, set[str]]:
    fm = _frontmatter(path.read_bytes())
    content_hash = str(fm.get("content_hash") or "")
    source_hash = str(fm.get("source_hash") or "") or None
    source_id = str(fm.get("source_id") or "") or None
    _validate_record_fields(fm, f"record {path.name}")
    if not HASH_RE.fullmatch(content_hash):
        raise ResultError(f"record {path.name} has no canonical content_hash")
    if fm.get("superseded_by"):
        raise ResultError(f"record {path.name} is retired")
    urls: set[str] = set()
    provenance = fm.get("provenance")
    for fields in [fm, provenance] if isinstance(provenance, dict) else [fm]:
        for key in ("source_url", "fetched_url", "also_published_at"):
            value = fields.get(key)
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, str) and item.strip():
                    urls.add(item.strip())
    return content_hash, source_hash, source_id, urls


def resolve_record(
    ingests_dir: Path, asset_hash: str, source_id: str, source_url: str = ""
) -> str:
    """Resolve exactly one live output record without using the scheduler intake hash."""
    store = ingests_dir / "store"
    asset_hash = asset_hash.removeprefix("sha256:")
    exact: dict[Path, str] = {}
    logical: dict[Path, str] = {}
    urls: dict[Path, str] = {}
    for path in sorted(store.glob("*.md")):
        try:
            content_hash, source_hash, record_source_id, record_urls = _record_values(
                path
            )
        except (OSError, ResultError):
            continue
        bare_content = content_hash.removeprefix("sha256:")
        bare_source = (source_hash or "").removeprefix("sha256:")
        relative = path.relative_to(ingests_dir).as_posix()
        if asset_hash and asset_hash in {bare_content, bare_source}:
            exact[path] = relative
        if source_id and record_source_id == source_id:
            logical[path] = relative
        if source_url and source_url in record_urls:
            urls[path] = relative
    matches = {**exact, **logical, **urls}
    if len(matches) != 1:
        raise ResultError(
            "could not resolve exactly one live record from the supplied identities: "
            f"found {len(matches)}"
        )
    return next(iter(matches.values()))


def written_record(ingests_dir: Path, log_path: Path) -> str:
    """Return the handler's one exact `Written:` record path, or empty if absent."""
    values = {
        line.removeprefix("Written: ").strip()
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("Written: ")
    }
    if not values:
        return ""
    if len(values) != 1:
        raise ResultError(f"handler reported {len(values)} different record paths")
    value = next(iter(values))
    if value.startswith("/mnt/output/"):
        value = str(ingests_dir / value.removeprefix("/mnt/output/"))
    return _relative_record_path(ingests_dir, value)


def _verify_record(
    ingests_dir: Path, record_path: str, content_hash: str | None, commit_sha: str
) -> tuple[str, str, str]:
    record_path = _relative_record_path(ingests_dir, record_path)
    if not COMMIT_RE.fullmatch(commit_sha):
        raise ResultError("commit_sha must be a full lowercase Git object ID")
    resolved_commit = str(
        _git(ingests_dir, "rev-parse", f"{commit_sha}^{{commit}}")
    ).strip()
    if resolved_commit != commit_sha:
        raise ResultError("commit_sha is not the canonical full commit ID")
    blob = _git(ingests_dir, "show", f"{commit_sha}:{record_path}", text=False)
    assert isinstance(blob, bytes)
    fm = _frontmatter(blob)
    _validate_record_fields(fm, "committed record")
    committed_hash = str(fm.get("content_hash") or "")
    if not HASH_RE.fullmatch(committed_hash):
        raise ResultError("committed record has no canonical content_hash")
    if content_hash is not None and committed_hash != content_hash:
        raise ResultError("content_hash does not match the committed record")
    if fm.get("superseded_by"):
        raise ResultError("committed record is retired")
    working_path = ingests_dir / record_path
    if not working_path.is_file() or working_path.read_bytes() != blob:
        raise ResultError("live record does not exactly match the committed record")
    return record_path, committed_hash, commit_sha


def _validate_record_fields(fm: dict, label: str) -> None:
    if fm.get("schema") not in RECORD_SCHEMAS:
        raise ResultError(f"{label} has an unsupported record schema")
    for field in ("title", "source_type"):
        if not isinstance(fm.get(field), str) or not fm[field].strip():
            raise ResultError(f"{label} has no {field}")


def _encode(payload: dict) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def publish_result(
    *,
    ingests_dir: Path,
    result_path: Path,
    run_uuid: str,
    outcome: str,
    record_path: str,
    commit_sha: str,
) -> bytes:
    run_uuid = _canonical_uuid(run_uuid)
    if not result_path.is_absolute():
        raise ResultError("result_path must be an absolute path")
    if outcome not in {"committed", "no-op"}:
        raise ResultError("outcome must be committed or no-op")
    record_path, content_hash, commit_sha = _verify_record(
        ingests_dir.resolve(), record_path, None, commit_sha
    )

    result_path.parent.mkdir(parents=True, exist_ok=True)
    if result_path.exists():
        raise ResultError("result_path already exists")
    payload = {
        "schema": SCHEMA,
        "run_uuid": run_uuid,
        "outcome": outcome,
        "record_path": record_path,
        "content_hash": content_hash,
        "commit_sha": commit_sha,
        "completed_at": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
    }
    encoded = _encode(payload)
    temp = result_path.with_name(f".{result_path.name}.{os.getpid()}.tmp")
    try:
        with temp.open("xb") as out:
            out.write(encoded + b"\n")
            out.flush()
            os.fsync(out.fileno())
        # A hard link publishes the complete inode atomically and, unlike replace(),
        # fails if another process has claimed this scheduler-owned result path.
        os.link(temp, result_path)
        directory_fd = os.open(result_path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temp.unlink(missing_ok=True)
    return encoded


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    resolve = sub.add_parser("resolve")
    resolve.add_argument("--ingests-dir", type=Path, required=True)
    resolve.add_argument("--asset-hash", default="")
    resolve.add_argument("--source-id", default="")
    resolve.add_argument("--source-url", default="")
    written = sub.add_parser("written")
    written.add_argument("--ingests-dir", type=Path, required=True)
    written.add_argument("--log-path", type=Path, required=True)
    publish = sub.add_parser("publish")
    publish.add_argument("--ingests-dir", type=Path, required=True)
    publish.add_argument("--result-path", type=Path, required=True)
    publish.add_argument("--run-uuid", required=True)
    publish.add_argument("--outcome", required=True)
    publish.add_argument("--record-path", required=True)
    publish.add_argument("--commit-sha", required=True)
    args = parser.parse_args()
    try:
        if args.command == "resolve":
            print(
                resolve_record(
                    args.ingests_dir, args.asset_hash, args.source_id, args.source_url
                )
            )
        elif args.command == "written":
            print(written_record(args.ingests_dir, args.log_path))
        else:
            encoded = publish_result(
                ingests_dir=args.ingests_dir,
                result_path=args.result_path,
                run_uuid=args.run_uuid,
                outcome=args.outcome,
                record_path=args.record_path,
                commit_sha=args.commit_sha,
            )
            sys.stdout.buffer.write(PREFIX.encode("ascii") + encoded + b"\n")
    except (OSError, ResultError) as exc:
        print(f"Error: cannot publish ingest result: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
