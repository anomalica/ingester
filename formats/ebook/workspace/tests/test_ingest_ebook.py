from extraction.epub_extract import ExtractedBook
from ingest_ebook import _build_frontmatter


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
