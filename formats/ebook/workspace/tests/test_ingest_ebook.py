import hashlib
import json
from unittest.mock import patch

from extraction.epub_extract import Chapter, ExtractedBook
from ingest_ebook import _build_frontmatter, run


def test_frontmatter_preserves_temporal_precision_and_offset():
    frontmatter = _build_frontmatter(
        ExtractedBook(title="Test book"),
        "2020-08",
        None,
        "2026-09-21T10:00:00+09:00",
        "a" * 64,
        None,
        None,
    )

    assert 'date_published: "2020-08"' in frontmatter
    assert 'date_accessed: "2026-09-21T10:00:00+09:00"' in frontmatter
    assert 'date_extracted: "' in frontmatter
    assert 'Z"' in frontmatter


def test_frontmatter_omits_unevidenced_publication_date():
    frontmatter = _build_frontmatter(
        ExtractedBook(title="Undated book"),
        None,
        None,
        "2026-09-21T10:00:00Z",
        "b" * 64,
        None,
        None,
    )

    assert "date_published:" not in frontmatter


def test_work_identifier_candidate_does_not_suppress_different_asset(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "asset.epub").write_bytes(b"different EPUB bytes")
    (staging / "manifest.json").write_text(
        json.dumps(
            {
                "asset": "asset.epub",
                "source": "fixture.epub",
                "fetched_at": "2026-09-22T09:00:00Z",
            }
        )
    )
    output = tmp_path / "output"
    store = output / "store"
    store.mkdir(parents=True)
    (store / f"{'b' * 64}.md").write_text(
        "---\n"
        "schema: anomalica/record/1\n"
        "title: Existing edition\n"
        "source_type: ebook\n"
        "source_id: isbn:9780000000000\n"
        f"content_hash: sha256:{'b' * 64}\n"
        "---\nExisting body.\n"
    )
    book = ExtractedBook(
        title="New edition",
        identifier="isbn:9780000000000",
        chapters=[Chapter(index=1, title="One", markdown="Different body.")],
    )

    with patch("ingest_ebook.extract", return_value=book):
        assert run(staging, output, force=False) == 0

    assert len(list(store.glob("*.md"))) == 2


def test_intermediate_path_uses_asset_hash_not_extracted_body_hash(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    asset = b"asset identity must name the intermediate"
    (staging / "asset.epub").write_bytes(asset)
    (staging / "manifest.json").write_text(
        json.dumps(
            {
                "asset": "asset.epub",
                "source": "fixture.epub",
                "fetched_at": "2026-09-22T09:00:00Z",
            }
        )
    )
    output = tmp_path / "output"
    book = ExtractedBook(
        title="Fixture",
        chapters=[Chapter(index=1, title="One", markdown="Shared prose.")],
    )

    with patch("ingest_ebook.extract", return_value=book):
        assert run(staging, output, force=False) == 0

    expected = hashlib.sha256(asset).hexdigest()
    assert [path.name for path in (output / "store").glob("*.md")] == [f"{expected}.md"]
