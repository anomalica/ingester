#!/usr/bin/env python3
"""Atomically establish one immutable content-addressed archive object."""

from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from pathlib import Path


class ArchiveError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_content_addressed(source: Path, target: Path, expected_hash: str) -> bool:
    """Create ``target`` atomically, or verify the immutable object already there.

    Returns ``True`` when this call creates the object and ``False`` when an
    identical regular file already exists. A temporary file is hard-linked into
    place so concurrent readers never observe a partial archive object.
    """
    expected_hash = expected_hash.removeprefix("sha256:")
    if len(expected_hash) != 64 or any(
        c not in "0123456789abcdef" for c in expected_hash
    ):
        raise ArchiveError("expected hash must be a full lowercase SHA-256")
    if "." not in target.name or target.name.rsplit(".", 1)[0] != expected_hash:
        raise ArchiveError("archive target name does not match the expected hash")
    if not source.is_file():
        raise ArchiveError(f"archive source is missing: {source}")
    if _sha256(source) != expected_hash:
        raise ArchiveError("archive source bytes do not match the expected hash")

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        if (
            target.is_symlink()
            or not target.is_file()
            or _sha256(target) != expected_hash
        ):
            raise ArchiveError(
                f"existing archive does not match its content-addressed path: {target}"
            )
        return False

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=target.parent, prefix=f".{target.name}.", delete=False
        ) as output:
            temporary = Path(output.name)
            with source.open("rb") as input_file:
                for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
                    output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o644)
        if _sha256(temporary) != expected_hash:
            raise ArchiveError("copied archive bytes do not match the expected hash")
        try:
            os.link(temporary, target)
        except FileExistsError:
            if (
                target.is_symlink()
                or not target.is_file()
                or _sha256(target) != expected_hash
            ):
                raise ArchiveError(
                    f"existing archive does not match its content-addressed path: {target}"
                )
            return False
        return True
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--expected-hash", required=True)
    args = parser.parse_args()
    try:
        created = archive_content_addressed(
            args.source, args.target, args.expected_hash
        )
    except (ArchiveError, OSError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    print("created" if created else "exists")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
