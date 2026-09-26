#!/usr/bin/env python3
"""CAS-bound legacy Record migration from held archive bytes.

The migration is prepared and committed in a detached temporary worktree, then
the caller's branch is advanced with ``git update-ref <new> <expected>``. The
paths changed by the migration must be clean. Unrelated in-progress review
edits remain untouched when the atomic ref change is materialised.
"""

from __future__ import annotations

import argparse
import fcntl
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(ROOT.parent / "anomalica-common" / "src"))

from record3 import Record3Error, migrate_legacy_record  # noqa: E402


class MigrationCommandError(RuntimeError):
    pass


def _git(repo: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    if process.returncode:
        raise MigrationCommandError(
            process.stderr.strip() or f"git {' '.join(args)} failed"
        )
    return process.stdout.strip()


def migrate(
    ingests_dir: Path,
    records_dir: Path,
    record: str,
    expected_head: str,
) -> str:
    ingests_dir = ingests_dir.resolve()
    records_dir = records_dir.resolve()
    common_dir = Path(_git(ingests_dir, "rev-parse", "--git-common-dir"))
    if not common_dir.is_absolute():
        common_dir = (ingests_dir / common_dir).resolve()
    lock_path = common_dir / "anomalica-write.lock"
    branch = _git(ingests_dir, "symbolic-ref", "HEAD")

    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if _git(ingests_dir, "rev-parse", "HEAD") != expected_head:
            raise MigrationCommandError("ingests HEAD changed from expected_head")

        temporary_root = Path(tempfile.mkdtemp(prefix="anomalica-record3-"))
        worktree = temporary_root / "ingests"
        try:
            _git(
                ingests_dir,
                "worktree",
                "add",
                "--quiet",
                "--detach",
                str(worktree),
                expected_head,
            )
            source = (worktree / record).resolve()
            try:
                source.relative_to(worktree)
            except ValueError as exc:
                raise MigrationCommandError(
                    "record path escapes the ingests repository"
                ) from exc
            result = migrate_legacy_record(source, records_dir, output_dir=worktree)
            pathspecs = ["store"]
            if (worktree / "by-name").exists():
                pathspecs.append("by-name")
            if (worktree / "media").exists():
                pathspecs.append("media")
            if (worktree / "source-maps").exists():
                pathspecs.append("source-maps")
            _git(worktree, "add", "-A", "--", *pathspecs)
            if not _git(worktree, "diff", "--cached", "--name-only"):
                raise MigrationCommandError("migration produced no repository change")
            short = result.content_hash.removeprefix("sha256:")[:12]
            _git(
                worktree,
                "-c",
                "user.name=Anomalica Ingester",
                "-c",
                "user.email=ingester@anomalica.is",
                "commit",
                "-m",
                f"migrate: upgrade legacy record to record/3 ({short})",
            )
            commit = _git(worktree, "rev-parse", "HEAD")
            changed = [
                name
                for name in _git(
                    worktree,
                    "diff",
                    "--no-renames",
                    "--name-only",
                    "-z",
                    expected_head,
                    commit,
                ).split("\0")
                if name
            ]
            if not changed or _git(
                ingests_dir,
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--",
                *changed,
            ):
                raise MigrationCommandError(
                    "migration targets have uncommitted or untracked changes"
                )
            _git(ingests_dir, "update-ref", branch, commit, expected_head)
            # Only the proven-clean paths are materialised. A repository-wide
            # reset would erase unrelated review edits in this shared worktree.
            _git(
                ingests_dir,
                "restore",
                "--source",
                commit,
                "--staged",
                "--worktree",
                "--",
                *changed,
            )
            return commit
        finally:
            try:
                _git(ingests_dir, "worktree", "remove", "--force", str(worktree))
            except MigrationCommandError:
                pass
            shutil.rmtree(temporary_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ingests-dir", type=Path, required=True)
    parser.add_argument("--records-dir", type=Path, required=True)
    parser.add_argument("--record", required=True, help="path relative to ingests")
    parser.add_argument("--expected-head", required=True)
    args = parser.parse_args()
    try:
        print(
            migrate(
                args.ingests_dir,
                args.records_dir,
                args.record,
                args.expected_head,
            )
        )
    except (MigrationCommandError, Record3Error, OSError) as exc:
        print(f"Error: migration failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
