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
    "web": 1,
    "ebook": 1,
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
    path.write_text(yaml.safe_dump(CURRENT_VERSIONS, sort_keys=True))
    return path
