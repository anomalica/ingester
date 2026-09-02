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
    by_name = tmp_path / "by-name"

    record_path, link_path = write_record(
        store_dir=store,
        by_name_dir=by_name,
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
    by_name = tmp_path / "by-name"
    by_name.mkdir(parents=True)

    stale = by_name / "2023-06-05-web-test-article.md"
    stale.symlink_to("/nonexistent")

    write_record(
        store, by_name, "abc123", "content", "2023-06-05", "web", "Test Article"
    )
    assert stale.resolve() == (store / "abc123.md").resolve()


def test_write_record_idempotent_when_symlink_already_correct(tmp_path):
    """Re-running with the same hash and slug is a no-op overwrite."""
    store = tmp_path / "store"
    by_name = tmp_path / "by-name"

    write_record(store, by_name, "abc123", "v1", "2023-06-05", "web", "Test Article")
    write_record(store, by_name, "abc123", "v2", "2023-06-05", "web", "Test Article")

    assert (store / "abc123.md").read_text() == "v2"
    link = by_name / "2023-06-05-web-test-article.md"
    assert link.resolve() == (store / "abc123.md").resolve()


def test_write_record_refuses_to_clobber_unrelated_record(tmp_path):
    """Symlink pointing to a different real record must not be overwritten."""
    store = tmp_path / "store"
    by_name = tmp_path / "by-name"

    write_record(store, by_name, "aaa111", "first", "2023-06-05", "web", "Test Article")

    with pytest.raises(SymlinkCollisionError):
        write_record(
            store, by_name, "bbb222", "second", "2023-06-05", "web", "Test Article"
        )

    link = by_name / "2023-06-05-web-test-article.md"
    assert link.resolve() == (store / "aaa111.md").resolve()
    assert (store / "aaa111.md").read_text() == "first"
    assert not (store / "bbb222.md").exists()


def test_write_record_force_retires_stale_record_to_v1(tmp_path):
    """With force=True, the colliding symlink is repointed and its old target is
    RETIRED to store/v1 with a superseded_by pointer - never deleted - so review
    work and possession proofs survive."""
    store = tmp_path / "store"
    by_name = tmp_path / "by-name"

    old = "---\ncontent_hash: sha256:aaa111\ntitle: T\n---\nfirst"
    write_record(store, by_name, "aaa111", old, "2023-06-05", "web", "Test Article")
    (store / "aaa111.verification.json").write_text("{}")
    (store / "aaa111.review.json").write_text('{"reviews": []}')

    write_record(
        store,
        by_name,
        "bbb222",
        "---\ncontent_hash: sha256:bbb222\ntitle: T\n---\nsecond",
        "2023-06-05",
        "web",
        "Test Article",
        force=True,
    )

    link = by_name / "2023-06-05-web-test-article.md"
    assert link.resolve() == (store / "bbb222.md").resolve()
    assert (store / "bbb222.md").read_text().endswith("second")
    # Old record retired to store/v1, not deleted, and stamped superseded_by.
    assert not (store / "aaa111.md").exists()
    retired = store / "v1" / "aaa111.md"
    assert retired.exists()
    assert "superseded_by: bbb222" in retired.read_text()
    # Sidecars - including the irreplaceable review.json - carried to v1.
    assert (store / "v1" / "aaa111.verification.json").exists()
    assert (store / "v1" / "aaa111.review.json").exists()
    assert not (store / "aaa111.review.json").exists()


def test_write_record_reuses_slug_on_same_hash_reingest(tmp_path):
    """A re-ingest of the same content_hash keeps its slug even when the re-derived
    title/date differ, so it does not leave a second alias standing for one record."""
    store = tmp_path / "store"
    by_name = tmp_path / "by-name"
    write_record(
        store,
        by_name,
        "abc123",
        "---\ncontent_hash: sha256:abc123\ntitle: Old\n---\nbody",
        "2020-05-14",
        "pdf",
        "Old Title",
        force=True,
    )
    # Re-ingest the SAME hash with a different title and date.
    write_record(
        store,
        by_name,
        "abc123",
        "---\ncontent_hash: sha256:abc123\ntitle: New\n---\nbody2",
        "2020-08-09",
        "pdf",
        "New Title",
        force=True,
    )
    links = [p for p in by_name.iterdir() if p.is_symlink()]
    assert len(links) == 1  # one alias, not two
    assert links[0].name == "2020-05-14-pdf-old-title.md"  # the ORIGINAL slug held
    assert links[0].resolve() == (store / "abc123.md").resolve()
    assert (store / "abc123.md").read_text().endswith("body2")  # content did update


def test_write_record_force_stamps_supersedes_on_the_replacement(tmp_path):
    store = tmp_path / "store"
    by_name = tmp_path / "by-name"
    old = "---\ncontent_hash: sha256:aaa111\ntitle: T\n---\nold body\n"
    write_record(store, by_name, "aaa111", old, "2020-01-01", "web", "Same Title")
    new = "---\ncontent_hash: sha256:bbb222\ntitle: T\n---\nnew body\n"
    path, _ = write_record(
        store, by_name, "bbb222", new, "2020-01-01", "web", "Same Title", force=True
    )
    text = path.read_text()
    assert "content_hash: sha256:bbb222\nsupersedes: aaa111\n" in text
    assert "superseded_by: bbb222" in (store / "v1" / "aaa111.md").read_text()
