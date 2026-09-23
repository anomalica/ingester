import pytest
import yaml

from pipeline_version import (
    CURRENT_VERSIONS,
    MANIFEST_NAME,
    current_version,
    stamp_pipeline_version,
    write_manifest,
)

SUPPORTED_SOURCE_TYPES = {"audio", "ebook", "image", "pdf", "video", "web"}


def test_every_supported_source_type_has_an_explicit_current_version():
    assert set(CURRENT_VERSIONS) == SUPPORTED_SOURCE_TYPES
    for media_type in SUPPORTED_SOURCE_TYPES:
        assert current_version(media_type) == CURRENT_VERSIONS[media_type]


def test_current_version_rejects_unregistered_type():
    with pytest.raises(ValueError, match="unsupported source_type: 'hologram'"):
        current_version("hologram")


def test_write_manifest_writes_full_map(tmp_path):
    store = tmp_path / "store"
    path = write_manifest(store)
    assert path == store / MANIFEST_NAME
    assert yaml.safe_load(path.read_text()) == CURRENT_VERSIONS


def test_write_manifest_creates_store_dir(tmp_path):
    store = tmp_path / "nested" / "store"
    write_manifest(store)
    assert (store / MANIFEST_NAME).exists()


def test_write_manifest_idempotent(tmp_path):
    store = tmp_path / "store"
    write_manifest(store)
    first = (store / MANIFEST_NAME).read_text()
    write_manifest(store)
    assert (store / MANIFEST_NAME).read_text() == first


def test_manifest_never_regresses_a_type(tmp_path):
    from pipeline_version import CURRENT_VERSIONS, write_manifest

    store = tmp_path / "store"
    store.mkdir()
    ahead = dict(CURRENT_VERSIONS)
    ahead["web"] = CURRENT_VERSIONS["web"] + 3
    (store / "_pipeline_versions.yaml").write_text(yaml.safe_dump(ahead))
    written = yaml.safe_load(write_manifest(store).read_text())
    assert written["web"] == CURRENT_VERSIONS["web"] + 3
    assert written["ebook"] == CURRENT_VERSIONS["ebook"]


def test_manifest_does_not_publish_unregistered_existing_types(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    existing = {**CURRENT_VERSIONS, "hologram": 9}
    (store / MANIFEST_NAME).write_text(yaml.safe_dump(existing))

    written = yaml.safe_load(write_manifest(store).read_text())

    assert written == CURRENT_VERSIONS


def test_manifest_replaces_a_malformed_existing_manifest(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    (store / MANIFEST_NAME).write_text("[not, a, map]\n")

    written = yaml.safe_load(write_manifest(store).read_text())

    assert written == CURRENT_VERSIONS


def test_stamp_pipeline_version_changes_only_the_generation(tmp_path):
    record = tmp_path / "record.md"
    record.write_text(
        "---\nsource_type: web\ndate_extracted: old\nprocessing:\n"
        "  pipeline_version: 6\n  version: abc1234\n---\nBody bytes.\n"
    )

    assert stamp_pipeline_version(record, "web", expected_previous=6)

    assert record.read_text() == (
        "---\nsource_type: web\ndate_extracted: old\nprocessing:\n"
        "  pipeline_version: 7\n  version: abc1234\n---\nBody bytes.\n"
    )


def test_stamp_pipeline_version_adds_a_processing_block(tmp_path):
    record = tmp_path / "record.md"
    record.write_text("---\nsource_type: pdf\ntitle: Example\n---\nBody bytes.\n")

    assert stamp_pipeline_version(record, "pdf", expected_previous=None)

    assert record.read_text() == (
        "---\nsource_type: pdf\ntitle: Example\nprocessing:\n"
        "  pipeline_version: 1\n---\nBody bytes.\n"
    )


def test_stamp_pipeline_version_refuses_a_mismatched_record(tmp_path):
    record = tmp_path / "record.md"
    original = "---\nsource_type: web\nprocessing:\n  pipeline_version: 5\n---\nBody.\n"
    record.write_text(original)

    with pytest.raises(ValueError, match="pipeline_version is 5, expected 6"):
        stamp_pipeline_version(record, "web", expected_previous=6)

    assert record.read_text() == original


def test_stamp_pipeline_version_refuses_record3_without_mutating_it(tmp_path):
    record = tmp_path / "record.md"
    original = (
        "---\nschema: anomalica/record/3\nsource_type: web\nprocessing:\n"
        "  asset_pipeline_versions:\n"
        f"  - asset_hash: sha256:{'a' * 64}\n"
        "    source_type: web\n"
        "    pipeline_version: 6\n---\nBody.\n"
    )
    record.write_text(original)

    with pytest.raises(ValueError, match="record/3 extraction generations"):
        stamp_pipeline_version(record, "web", expected_previous=None)

    assert record.read_text() == original
