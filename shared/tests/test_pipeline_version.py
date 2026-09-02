import yaml

from pipeline_version import (
    CURRENT_VERSIONS,
    MANIFEST_NAME,
    current_version,
    write_manifest,
)


def test_current_version_known_types():
    for media_type in ("pdf", "web", "ebook", "audio", "video"):
        assert current_version(media_type) == CURRENT_VERSIONS[media_type]


def test_current_version_unknown_type_defaults_to_one():
    assert current_version("hologram") == 1


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
