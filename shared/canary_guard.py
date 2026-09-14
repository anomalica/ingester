"""Fail-closed validation for a generation-rerender canary before commit."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import yaml

from pipeline_version import current_version
from validator import validate

_EXTRACTION_OWNED = {
    "date_extracted",
    "processing",
    "quality",
    "review_carryover",
}


class CanaryError(ValueError):
    pass


def _split(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise CanaryError("record has no YAML frontmatter")
    raw, body = text[4:].split("\n---\n", 1)
    frontmatter = yaml.safe_load(raw)
    if not isinstance(frontmatter, dict):
        raise CanaryError("record frontmatter is not a mapping")
    return frontmatter, body


def validate_candidate(
    parent: str,
    candidate: str,
    *,
    content_hash: str,
    reviewed: bool,
) -> None:
    old, old_body = _split(parent)
    new, new_body = _split(candidate)
    expected_hash = f"sha256:{content_hash}"
    if (
        old.get("content_hash") != expected_hash
        or new.get("content_hash") != expected_hash
    ):
        raise CanaryError("content identity does not match the record path")
    if new.get("schema") != old.get("schema"):
        raise CanaryError("record schema changed")
    if new.get("source_type") != old.get("source_type"):
        raise CanaryError("source type changed")

    old_protected = {k: v for k, v in old.items() if k not in _EXTRACTION_OWNED}
    new_protected = {k: v for k, v in new.items() if k not in _EXTRACTION_OWNED}
    if new_protected != old_protected:
        raise CanaryError("identity, provenance, rights, or curated metadata changed")

    processing = new.get("processing")
    if not isinstance(processing, dict) or processing.get(
        "pipeline_version"
    ) != current_version(str(new["source_type"])):
        raise CanaryError(
            "candidate does not declare the current extraction generation"
        )

    expected_schema = str(old["schema"])
    errors = validate(candidate, expected_schema=expected_schema).errors
    if errors:
        raise CanaryError("candidate does not validate: " + "; ".join(errors))

    if (
        reviewed
        and new.get("source_type") in {"audio", "video"}
        and new_body != old_body
    ):
        raise CanaryError("reviewed audio/video body changed")

    if reviewed and new_body != old_body:
        carryover = new.get("review_carryover")
        if not isinstance(carryover, dict) or carryover.get("from") != content_hash:
            raise CanaryError("changed reviewed body has no valid review_carryover")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ingests-dir", required=True, type=Path)
    parser.add_argument("--parent", required=True)
    parser.add_argument("--record-path", required=True)
    parser.add_argument("--candidate-path", required=True, type=Path)
    args = parser.parse_args()

    repo = args.ingests_dir.resolve()
    record_path = Path(args.record_path)
    if record_path.is_absolute() or ".." in record_path.parts:
        raise CanaryError("record path must be repository-relative")
    parent = _git(repo, "show", f"{args.parent}:{record_path.as_posix()}")
    live = (repo / record_path).read_text(encoding="utf-8")
    if live != parent:
        raise CanaryError("live record differs from the exact parent before acceptance")
    candidate = args.candidate_path.read_text(encoding="utf-8")
    content_hash = record_path.name.split(".", 1)[0]
    reviewed = (repo / "store" / f"{content_hash}.review.json").exists()
    validate_candidate(parent, candidate, content_hash=content_hash, reviewed=reviewed)

    changed = set(
        _git(
            repo, "diff", "--name-only", args.parent, "--", "store", "by-name", "media"
        )
        .strip()
        .splitlines()
    )
    changed.update(
        _git(
            repo,
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            "store",
            "by-name",
            "media",
        )
        .strip()
        .splitlines()
    )
    if changed:
        raise CanaryError(
            "canary changed live output paths: " + json.dumps(sorted(changed))
        )
    print(f"Canary candidate validated against {args.parent}: {record_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
