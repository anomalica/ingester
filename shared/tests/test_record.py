import pytest

import record
from record import (
    SymlinkCollisionError,
    clean_title,
    get_version,
    normalise_classification,
    slugify,
    symlink_name,
    write_record,
)


def test_clean_title_strips_undefined():
    assert clean_title("Misrep undefined-7816710") == "Misrep 7816710"
    assert clean_title("None-Report") == "Report"
    assert clean_title("Normal Title") == "Normal Title"
    # cleaning that would empty the title returns the original
    assert clean_title("undefined") == "undefined"


def test_no_title_published_prelude_injection():
    """Regression guard. The ingester must NOT inject a `# {title}` + `*Published
    {date}*` prelude into the record body - it duplicates the title (frontmatter
    already carries it) and re-appears on every re-ingest. The prelude helpers
    were removed; re-adding them reintroduces the double-title bug (they were
    stripped from the data by 8780ddd but the code was left injecting it)."""
    assert not hasattr(record, "body_prelude")
    assert not hasattr(record, "inject_body_prelude")


def test_normalise_classification_lifts_banner_and_strips():
    rec = (
        '---\nschema: anomalica/record/1\ntitle: "Misrep"\nsource_type: pdf\n---\n\n'
        "~~(SECRET//REL TO USA, FVEY)~~ The mission began. (U) This paragraph is "
        "unclassified.\n"
    )
    out = normalise_classification(rec)
    assert 'classification: "SECRET//REL TO USA, FVEY"' in out
    assert "~~" not in out  # strikethrough banner removed
    assert "(SECRET//REL TO USA, FVEY)" not in out.split("---", 2)[2]


def test_normalise_classification_noop_without_markings():
    rec = (
        '---\nschema: anomalica/record/1\ntitle: "Doc"\nsource_type: pdf\n---\n\n'
        "The (c) here is a subsection reference, not a marking.\n"
    )
    assert normalise_classification(rec) == rec


def test_slugify_basic():
    assert slugify("Hello World") == "hello-world"


def test_slugify_special_chars():
    assert slugify("Glowing Auras and 'Black Money'") == "glowing-auras-and-black-money"


def test_slugify_truncates():
    long_title = "A" * 100
    result = slugify(long_title, max_length=60)
    assert len(result) <= 60


def test_slugify_strips_trailing_hyphens():
    assert not slugify("Hello---World---").endswith("-")


def test_symlink_name():
    name = symlink_name("2023-06-05", "web", "Some Article Title")
    assert name == "2023-06-05-web-some-article-title.md"


def test_get_version_returns_string():
    version = get_version()
    assert isinstance(version, str)
    assert len(version) > 0
    assert version != "unknown"


def test_write_record_creates_files(tmp_path):
    store = tmp_path / "store"
    records = tmp_path / "records"

    record_path, link_path = write_record(
        store_dir=store,
        records_dir=records,
        hex_hash="abc123",
        content="---\ntitle: Test\n---\nBody",
        date="2023-06-05",
        source_type="web",
        title="Test Article",
    )

    assert record_path.exists()
    assert record_path.read_text() == "---\ntitle: Test\n---\nBody"
    assert not (store / "abc123.meta.json").exists()
    assert link_path.is_symlink()
    assert link_path.resolve() == record_path.resolve()
    assert link_path.name == "2023-06-05-web-test-article.md"


def test_write_record_replaces_stale_symlink(tmp_path):
    """Stale symlinks (broken target) are safe to overwrite."""
    store = tmp_path / "store"
    records = tmp_path / "records"
    records.mkdir(parents=True)

    stale = records / "2023-06-05-web-test-article.md"
    stale.symlink_to("/nonexistent")

    write_record(
        store, records, "abc123", "content", "2023-06-05", "web", "Test Article"
    )
    assert stale.resolve() == (store / "abc123.md").resolve()


def test_write_record_idempotent_when_symlink_already_correct(tmp_path):
    """Re-running with the same hash and slug is a no-op overwrite."""
    store = tmp_path / "store"
    records = tmp_path / "records"

    write_record(store, records, "abc123", "v1", "2023-06-05", "web", "Test Article")
    write_record(store, records, "abc123", "v2", "2023-06-05", "web", "Test Article")

    assert (store / "abc123.md").read_text() == "v2"
    link = records / "2023-06-05-web-test-article.md"
    assert link.resolve() == (store / "abc123.md").resolve()


def test_write_record_refuses_to_clobber_unrelated_record(tmp_path):
    """Symlink pointing to a different real record must not be overwritten."""
    store = tmp_path / "store"
    records = tmp_path / "records"

    write_record(store, records, "aaa111", "first", "2023-06-05", "web", "Test Article")

    with pytest.raises(SymlinkCollisionError):
        write_record(
            store, records, "bbb222", "second", "2023-06-05", "web", "Test Article"
        )

    link = records / "2023-06-05-web-test-article.md"
    assert link.resolve() == (store / "aaa111.md").resolve()
    assert (store / "aaa111.md").read_text() == "first"
    assert not (store / "bbb222.md").exists()


def test_write_record_force_replaces_stale_record(tmp_path):
    """With force=True, a colliding symlink AND its target are replaced."""
    store = tmp_path / "store"
    records = tmp_path / "records"

    write_record(store, records, "aaa111", "first", "2023-06-05", "web", "Test Article")
    # Sidecar that should also be cleaned up under --force
    (store / "aaa111.verification.json").write_text("{}")

    write_record(
        store,
        records,
        "bbb222",
        "second",
        "2023-06-05",
        "web",
        "Test Article",
        force=True,
    )

    link = records / "2023-06-05-web-test-article.md"
    assert link.resolve() == (store / "bbb222.md").resolve()
    assert (store / "bbb222.md").read_text() == "second"
    assert not (store / "aaa111.md").exists()
    assert not (store / "aaa111.verification.json").exists()
