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
    """Reconcile classification markings to the ingest-format conventions:
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


def _stamp_superseded_by(text: str, new_hash: str) -> str:
    """Insert a superseded_by pointer into a record's frontmatter, after
    source_hash if present else content_hash else the opening fence. Idempotent."""
    if re.search(r"^superseded_by:", text, re.MULTILINE):
        return text
    stamp = (
        f"superseded_by: {new_hash}\n"
        f'superseded_reason: "Superseded by a --force re-ingest of the same source '
        f"(new content_hash {new_hash[:12]}). Retired to store/v1; the body stays "
        f'resolvable via the normative store/ -> store/v1 fallback."\n'
    )
    anchor = re.search(r"^source_hash:.*\n", text, re.MULTILINE) or re.search(
        r"^content_hash:.*\n", text, re.MULTILINE
    )
    if anchor:
        return text[: anchor.end()] + stamp + text[anchor.end() :]
    if text.startswith("---\n"):
        return "---\n" + stamp + text[len("---\n") :]
    return text


def _retire_to_v1(old_record: Path, new_hash: str) -> None:
    """Move a superseded record and its sidecars to store/v1 with a superseded_by
    stamp, rather than deleting them. A --force re-ingest must NOT bare-delete the
    prior record: anything downstream can still resolve it by hash (a digest, a
    redigest sweep), and a review.json on it is irreplaceable human work. The
    normative resolution order (store/ -> store/v1 via superseded_by -> reported
    dangling) requires the retired body to stay readable at store/v1/{hash}.md."""
    v1_dir = old_record.parent / "v1"
    v1_dir.mkdir(parents=True, exist_ok=True)
    stamped = _stamp_superseded_by(
        old_record.read_text(encoding="utf-8", errors="replace"), new_hash
    )
    old_record.write_text(stamped)
    stem = old_record.name[: -len(".md")]
    old_record.replace(v1_dir / old_record.name)
    # Carry sidecars (verification.json, review.json) alongside so review work and
    # possession proofs survive the retirement. Skip other .md files (a .v2 variant
    # is a distinct record, not a sidecar).
    for sidecar in old_record.parent.glob(f"{stem}.*"):
        if sidecar.suffix == ".md":
            continue
        sidecar.replace(v1_dir / sidecar.name)


def _existing_slug_for(records_dir: Path, record_path: Path) -> Path | None:
    """An existing records/ symlink that already resolves to record_path, or None.

    Used to keep a record's human-readable slug STABLE across a re-ingest of the
    same content_hash: the slug is a downstream join key (digests, queues, links),
    so a re-extraction must reuse it rather than mint a new one from a re-derived
    title/date and leave the old symlink orphaned as a second alias."""
    if not records_dir.is_dir():
        return None
    target = record_path.resolve()
    for link in records_dir.iterdir():
        if link.is_symlink():
            try:
                if (records_dir / os.readlink(link)).resolve() == target:
                    return link
            except OSError:
                continue
    return None


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
            When ``force`` is True, the colliding symlink is repointed and the
            old record it pointed at is RETIRED to store/v1 with a superseded_by
            pointer (never deleted), since --force re-ingest is an explicit
            replace that must not bare-drop a record downstream may resolve.
    """
    store_dir.mkdir(parents=True, exist_ok=True)
    records_dir.mkdir(parents=True, exist_ok=True)

    record_path = store_dir / f"{hex_hash}{variant}.md"
    # Reuse the existing slug on a same-content_hash re-ingest so the human alias
    # stays stable; only mint a new slug for a genuinely new record.
    existing_link = _existing_slug_for(records_dir, record_path)
    if existing_link is not None:
        link_path = existing_link
    else:
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
            # --force replace: RETIRE the prior record to store/v1 with a
            # superseded_by pointer rather than deleting it. Deleting bare-drops a
            # record anything downstream may resolve by hash and loses any review
            # work on it. See _retire_to_v1.
            _retire_to_v1(existing_target, hex_hash)
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
