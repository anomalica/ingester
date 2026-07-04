"""Record writing utilities - store files and human-readable symlinks."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

INGESTER_VERSION_ENV = "INGESTER_VERSION"


def get_version() -> str:
    """Get the short git commit hash of the ingester repository.

    Reads from the INGESTER_VERSION environment variable first (set by the
    host script for containerised runs where .git/ is not available).
    Falls back to running git rev-parse on the local checkout.
    """
    env_version = os.environ.get(INGESTER_VERSION_ENV)
    if env_version:
        return env_version

    try:
        repo_root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return "unknown"


def slugify(text: str, max_length: int = 60) -> str:
    """Convert text to a URL-safe slug."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    if len(text) > max_length:
        text = text[:max_length].rsplit("-", 1)[0]
    return text


def symlink_name(date: str, source_type: str, title: str, variant: str = "") -> str:
    """Generate the human-readable symlink filename. ``variant`` (e.g. ".v2")
    is inserted before the extension to keep parallel records distinct."""
    slug = slugify(title)
    return f"{date}-{source_type}-{slug}{variant}.md"


# A classification marking: a level, optionally followed by // or / and
# control/dissemination markings (NOFORN, REL TO ..., FOUO, SI, RELIDO...).
_CLASS_MARKING = (
    r"(?:TOP SECRET|SECRET|CONFIDENTIAL|UNCLASSIFIED|TS|S|C|U)"
    r"(?:(?://|/)[A-Z0-9][A-Z0-9 ,/]*)?"
)
# Struck-through marking - strikethrough + a classification token is an
# unambiguous banner (~~(SECRET//REL TO USA, FVEY)~~, ~~SECRET~~).
_STRUCK_CLASS_RE = re.compile(rf"~~\(?({_CLASS_MARKING})\)?~~")
# Parenthetical marking, unambiguous because it carries control markings
# (the //) or spells out a full level word.
_FULL_PAREN_CLASS_RE = re.compile(
    r"\(((?:TOP SECRET|SECRET|CONFIDENTIAL|UNCLASSIFIED)(?:(?://|/)[A-Z0-9][A-Z0-9 ,/]*)?"
    r"|(?:TS|S|C|U)(?://|/)[A-Z0-9][A-Z0-9 ,/]*)\)"
)
# Bare single-letter portion marking - only matched at line/heading start
# (portion-marking position) so we don't touch "(c)" subsections mid-prose.
_LINESTART_BARE_CLASS_RE = re.compile(r"(?m)^([ \t]*(?:#+[ \t]+)?)\((TS|S|C|U)\)[ \t]*")


def _quote_class_value(value: str) -> str:
    """Quote a classification value for inline annotation if it carries
    YAML-significant characters."""
    if any(ch in value for ch in (":", ",")):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def normalise_classification(record_text: str) -> str:
    """Reconcile classification markings to the record-format conventions:
    lift the document banner to frontmatter `classification`, drop redundant
    in-body repeats of that banner, and convert portion markings that differ
    from the banner to inline `{{classification: VALUE}}` annotations. Strips
    any strikethrough wrapping (classification is never struck text).

    Belt-and-braces over the extraction prompt: produces uniform output
    regardless of model variance, and lets existing records be fixed
    without re-running extraction.
    """
    parts = record_text.split("---", 2)
    if len(parts) < 3:
        return record_text
    frontmatter, body = parts[1], parts[2]

    candidates: list[str] = []
    candidates += [m.group(1) for m in _STRUCK_CLASS_RE.finditer(body)]
    candidates += [m.group(1) for m in _FULL_PAREN_CLASS_RE.finditer(body)]

    # Existing frontmatter banner wins; else derive from the body (prefer a
    # marking carrying control markings, else the first full marking).
    fm_match = re.search(r"(?m)^classification:\s*\"?([^\"\n]+)\"?\s*$", frontmatter)
    banner = fm_match.group(1).strip() if fm_match else None
    if banner is None:
        banner = next((c for c in candidates if "//" in c), None)
        if banner is None and candidates:
            banner = candidates[0]
        if banner:
            frontmatter = (
                frontmatter.rstrip("\n")
                + f'\nclassification: "{banner.replace(chr(34), chr(92) + chr(34))}"\n'
            )

    def _replace(value: str) -> str:
        # Redundant repeat of the document banner -> drop entirely.
        if banner and value == banner:
            return ""
        # Portion marking that differs -> inline annotation.
        return f"{{{{classification: {_quote_class_value(value)}}}}}"

    body = _STRUCK_CLASS_RE.sub(lambda m: _replace(m.group(1)), body)
    body = _FULL_PAREN_CLASS_RE.sub(lambda m: _replace(m.group(1)), body)
    body = _LINESTART_BARE_CLASS_RE.sub(
        lambda m: m.group(1) + _replace(m.group(2)) + " ", body
    )
    # Tidy whitespace the removed/replaced markings leave behind.
    body = re.sub(r"[ \t]{2,}", " ", body)
    body = re.sub(r"[ \t]+\n", "\n", body)

    return f"---{frontmatter}---{body}"


_TITLE_PLACEHOLDER_RE = re.compile(r"\b(?:undefined|null|none)\b[-\s]*", re.IGNORECASE)


def clean_title(title: str) -> str:
    """Remove leaked placeholder words ("undefined", "null", "None") that an
    extraction model sometimes substitutes for a missing title field, e.g.
    "Misrep undefined-7816710" -> "Misrep 7816710". These are not cosmetic:
    a malformed title propagates into the digester's knowledge graph as a
    node name. Returns the original title unchanged if cleaning would empty
    it.
    """
    cleaned = _TITLE_PLACEHOLDER_RE.sub("", title)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -–—")
    return cleaned or title


class SymlinkCollisionError(Exception):
    """Raised when a record symlink would clobber an unrelated existing record.

    Indicates that the upstream dedup pipeline (source_id / source_url /
    content_hash) failed to catch a re-ingest of the same logical source,
    or that two distinct sources produced the same human-readable slug.
    Either way, silently overwriting would orphan the existing store entry.
    """


def write_record(
    store_dir: Path,
    records_dir: Path,
    hex_hash: str,
    content: str,
    date: str,
    source_type: str,
    title: str,
    force: bool = False,
    variant: str = "",
) -> tuple[Path, Path]:
    """Write a record to the store and create a symlink in records/.

    ``variant`` (e.g. ".v2") is inserted before the ``.md`` extension on both
    the store file and the symlink, so a parallel record (such as the
    word-level v2 output) lives alongside the original without overwriting it.

    Returns:
        Tuple of (record_path, symlink_path).

    Raises:
        SymlinkCollisionError: if the target symlink already exists and
            points to a different real record. Stale symlinks (broken
            target) and idempotent re-writes (same target) are allowed.
            When ``force`` is True, the colliding symlink AND the old
            record file it points at are deleted before the new record
            is written, since --force re-ingest is an explicit replace.
    """
    store_dir.mkdir(parents=True, exist_ok=True)
    records_dir.mkdir(parents=True, exist_ok=True)

    record_path = store_dir / f"{hex_hash}{variant}.md"
    link_name = symlink_name(date, source_type, title, variant=variant)
    link_path = records_dir / link_name

    if link_path.is_symlink():
        existing_target = (records_dir / os.readlink(link_path)).resolve()
        if existing_target.exists() and existing_target != record_path.resolve():
            if not force:
                raise SymlinkCollisionError(
                    f"refusing to overwrite {link_path.name}: "
                    f"already points to {existing_target.name} (a different record). "
                    f"Upstream dedup should have caught this re-ingest."
                )
            # --force replace: drop the stale record file too so it does
            # not orphan in the store. Sidecar files (verification JSON)
            # that share the same hash stem are removed alongside.
            old_stem = existing_target.stem
            for sibling in existing_target.parent.glob(f"{old_stem}*"):
                sibling.unlink()
        link_path.unlink()
    elif link_path.exists():
        link_path.unlink()

    record_path.write_text(content)

    rel_target = os.path.relpath(record_path, records_dir)
    link_path.symlink_to(rel_target)

    # Refresh the store's pipeline-version manifest on every write so consumers
    # always see the current generation per media type (idempotent). Imported
    # locally to tolerate both shared-module import conventions in this repo:
    # flat `pipeline_version` (audio/web/ebook, shared/ on PYTHONPATH) and
    # `shared.pipeline_version` (the pdf handler, via a shared/ symlink).
    try:
        from pipeline_version import write_manifest
    except ModuleNotFoundError:
        from shared.pipeline_version import write_manifest
    write_manifest(store_dir)

    return record_path, link_path
