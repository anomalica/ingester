import json

from record import slugify, symlink_name, write_record


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


def test_write_record_creates_files(tmp_path):
    store = tmp_path / "store"
    records = tmp_path / "records"
    metadata = {"input_url": "https://example.com"}

    record_path, link_path = write_record(
        store_dir=store,
        records_dir=records,
        hex_hash="abc123",
        content="---\ntitle: Test\n---\nBody",
        metadata=metadata,
        date="2023-06-05",
        source_type="web",
        title="Test Article",
    )

    assert record_path.exists()
    assert record_path.read_text() == "---\ntitle: Test\n---\nBody"
    assert (store / "abc123.meta.json").exists()
    assert json.loads((store / "abc123.meta.json").read_text()) == metadata
    assert link_path.is_symlink()
    assert link_path.resolve() == record_path.resolve()
    assert link_path.name == "2023-06-05-web-test-article.md"


def test_write_record_overwrites_existing_symlink(tmp_path):
    store = tmp_path / "store"
    records = tmp_path / "records"
    records.mkdir(parents=True)

    # Create a stale symlink
    stale = records / "2023-06-05-web-test-article.md"
    stale.symlink_to("/nonexistent")

    write_record(
        store, records, "abc123", "content", {}, "2023-06-05", "web", "Test Article"
    )
    assert stale.resolve() == (store / "abc123.md").resolve()
