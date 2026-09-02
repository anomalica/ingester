"""Per-media-type pipeline version registry and the store manifest.

The pipeline version is a monotonic integer per media type, bumped by a
maintainer when extraction OUTPUT changes in a way that warrants re-ingesting
existing records - a new annotation type, a better model, a different
segmentation - NOT on every commit, so it cannot be derived from git. It drives
staleness and backfill, distinct from the on-disk ``schema`` (format) and the
``processing.version`` git short-hash (fine provenance). See
``anomalica/decisions/0040-pipeline-versioning-and-supersession.md``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# Current extraction generation per media type (source_type). Bump a type when
# a re-ingest of existing records is warranted. A record whose
# processing.pipeline_version is below the current value - or absent, which
# consumers treat as 0 - is stale and a backfill target.
CURRENT_VERSIONS: dict[str, int] = {
    "pdf": 1,
    # v2: strip page furniture before extraction, and capture images as media
    # bytes + structured `<!-- image: file/alt/caption -->` annotations (was
    # markdown ![](remote-url) with the caption as loose italic prose).
    # v3: inline emphasis is unwrapped from the DOM before extraction. v2 bodies
    # carry trafilatura's mangled emphasis markers and, around every bold or
    # italic span, split paragraphs, re-ordered fragments and dropped clauses.
    # v4: an image the extractor dropped is placed by the text that follows it
    # (its caption), and that caption folds into the annotation. v3 put a lead
    # picture after the byline when the page header repeated the author's name.
    # v5: anchors are whole blocks of text (a caption of short link fragments
    # is one line), so a lead picture is placed by its caption, not dropped.
    # v6: recirculation widgets ("Recommended Stories" with thumbnails) are
    # stripped before extraction.
    # v7: a file-only image annotation carried from an earlier generation with
    # no counterpart in the fresh extraction is dropped, not appended.
    "web": 7,
    # v2: emit printed_page markers from EPUB3 pagebreaks (previously discarded).
    # v3: chapter numbers are the printed ones, not the spine index; drop-cap
    # first letters rejoined to their word; footnotes resolved to markers.
    # v4: a dedicated notes document is dropped only once its notes were pulled
    # into the citing chapters; v3 dropped it on the strength of a few links.
    # v5: a document nothing was pulled from is never dropped as spent, and a
    # notes document under a Text/ prefix is found by its bare name (v4 dropped
    # six chapters of one book on a failed lookup).
    "ebook": 5,
    "audio": 1,
    "video": 1,
}

MANIFEST_NAME = "_pipeline_versions.yaml"


def current_version(media_type: str) -> int:
    """The current pipeline version for a media type. An unregistered type is
    generation 1 - a new handler is v1 until it declares a bump."""
    return CURRENT_VERSIONS.get(media_type, 1)


def write_manifest(store_dir: Path) -> Path:
    """Write the ``{media_type: current_version}`` manifest into the store.

    Refreshed on every ingest so consumers (e.g. the workbench) can read the
    current version per media type for staleness badging. Writes the full
    authoritative map; idempotent.
    """
    store_dir.mkdir(parents=True, exist_ok=True)
    path = store_dir / MANIFEST_NAME
    # Never regress a type: a container that started before a bump still holds
    # the old registry and would otherwise write it back over the new one.
    versions = dict(CURRENT_VERSIONS)
    try:
        existing = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        existing = {}
    for media_type, version in existing.items():
        if isinstance(version, int) and version > versions.get(media_type, 0):
            versions[media_type] = version
    path.write_text(yaml.safe_dump(versions, sort_keys=True))
    return path
