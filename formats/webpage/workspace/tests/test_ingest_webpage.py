import json
from datetime import date
from unittest.mock import patch

from extraction.trafilatura_ext import Article

import ingest_webpage


SAMPLE_ARTICLE = Article(
    text="# Test Article\n\nFirst paragraph of article content.\n\nSecond paragraph.",
    title="Test Article",
    authors=["Jane Smith"],
    date="2023-06-05",
    sitename="Example News",
    description="A test article",
)


def _create_staging(
    tmp_path,
    html="<html><body>Article</body></html>",
    url="https://example.com/article",
):
    """Create a staging directory with a manifest and HTML asset."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "asset.html").write_text(html)
    manifest = {
        "source": url,
        "asset": "asset.html",
        "detected_type": "text/html",
        "fetch_method": "http",
        "fetched_at": "2026-03-28T10:00:00Z",
    }
    (staging / "manifest.json").write_text(json.dumps(manifest))
    return staging


@patch("ingest_webpage.extract_article", return_value=SAMPLE_ARTICLE)
def test_ingest_writes_record_to_store(mock_extract, tmp_path):
    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_webpage.run(staging, output, force=False)
    md_files = list((output / "store").glob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text()
    assert "schema: anomalica/record/1" in content
    assert "source_type: web" in content
    assert "source_url: https://example.com/article" in content
    assert "Test Article" in content


@patch("ingest_webpage.extract_article", return_value=SAMPLE_ARTICLE)
def test_ingest_writes_creators_not_authors(mock_extract, tmp_path):
    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_webpage.run(staging, output, force=False)
    content = list((output / "store").glob("*.md"))[0].read_text()
    assert "creators:" in content
    assert "- Jane Smith" in content
    assert "authors:" not in content


@patch("ingest_webpage.extract_article", return_value=SAMPLE_ARTICLE)
def test_ingest_includes_processing_in_frontmatter(mock_extract, tmp_path):
    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_webpage.run(staging, output, force=False)
    md_files = list((output / "store").glob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text()
    assert "processing:" in content
    assert "handler: webpage" in content
    assert "name: trafilatura" in content
    assert "role: extraction" in content
    assert "provider: local" in content
    assert "date_extracted:" in content


@patch("ingest_webpage.extract_article", return_value=SAMPLE_ARTICLE)
def test_ingest_creates_symlink(mock_extract, tmp_path):
    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_webpage.run(staging, output, force=False)
    links = list((output / "by-name").glob("*.md"))
    assert len(links) == 1
    assert links[0].is_symlink()
    assert "2023-06-05-web-test-article" in links[0].name


@patch("ingest_webpage.extract_article", return_value=SAMPLE_ARTICLE)
def test_ingest_skips_when_exists(mock_extract, tmp_path):
    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_webpage.run(staging, output, force=False)
    ingest_webpage.run(staging, output, force=False)
    md_files = list((output / "store").glob("*.md"))
    assert len(md_files) == 1


@patch("ingest_webpage.extract_article", return_value=SAMPLE_ARTICLE)
def test_ingest_re_extracts_with_force(mock_extract, tmp_path):
    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_webpage.run(staging, output, force=False)
    ingest_webpage.run(staging, output, force=True)
    assert mock_extract.call_count == 2


@patch("ingest_webpage.extract_article", return_value=None)
def test_ingest_exits_when_extraction_fails(mock_extract, tmp_path):
    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    exit_code = ingest_webpage.run(staging, output, force=False)
    assert exit_code != 0


def test_ingest_exits_when_no_manifest(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    output = tmp_path / "output"
    exit_code = ingest_webpage.run(staging, output, force=False)
    assert exit_code != 0


def test_ingest_exits_when_no_asset(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    manifest = {"source": "https://example.com", "asset": "asset.html"}
    (staging / "manifest.json").write_text(json.dumps(manifest))
    output = tmp_path / "output"
    exit_code = ingest_webpage.run(staging, output, force=False)
    assert exit_code != 0


# --- Wayback capture-date guard ---


def test_wayback_capture_date_parses_timestamp():
    assert ingest_webpage._wayback_capture_date(
        "https://web.archive.org/web/20010413101341/http://x.com/a"
    ) == date(2001, 4, 13)


def test_wayback_capture_date_none_for_non_wayback():
    assert ingest_webpage._wayback_capture_date("https://x.com/a") is None
    assert ingest_webpage._wayback_capture_date(None) is None


def test_date_from_url_recovers_six_digit_slug():
    assert (
        ingest_webpage._date_from_url(
            "http://www.space.com/peopleinterviews/clarke_believe_010227.html",
            not_after=date(2001, 4, 13),
        )
        == "2001-02-27"
    )


def test_date_from_url_recovers_eight_digit_slug():
    assert (
        ingest_webpage._date_from_url(
            "https://x.com/2015/03/28/story.html", not_after=date(2016, 1, 1)
        )
        == "2015-03-28"
    )


def test_date_from_url_rejects_when_only_parse_is_after_capture():
    # 030615 -> 2003-06-15 (after the capture) or 1903-06-15 (below the 1990
    # floor): neither is a valid publication date, so nothing is recovered.
    assert (
        ingest_webpage._date_from_url(
            "http://x.com/story_030615.html", not_after=date(2002, 1, 1)
        )
        is None
    )


def test_date_from_url_rejects_impossible_date():
    # 993401 -> month 34: not a calendar date under either century.
    assert (
        ingest_webpage._date_from_url(
            "http://x.com/id_993401.html", not_after=date(2020, 1, 1)
        )
        is None
    )


def test_date_from_url_none_without_date_digits():
    assert (
        ingest_webpage._date_from_url(
            "http://x.com/story/interview.html", not_after=date(2020, 1, 1)
        )
        is None
    )


def _create_wayback_staging(tmp_path, url, fetched_url):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "asset.html").write_text("<html><body>Article</body></html>")
    manifest = {
        "source": url,
        "asset": "asset.html",
        "detected_type": "text/html",
        "fetch_method": "wayback",
        "fetched_at": "2026-03-28T10:00:00Z",
        "fetched_url": fetched_url,
    }
    (staging / "manifest.json").write_text(json.dumps(manifest))
    return staging


def test_wayback_capture_date_not_used_as_publication_date(tmp_path):
    """Reproduces the Clarke case: the extractor returns the Wayback capture date
    (2001-04-13) but the record must carry the URL-slug publication date."""
    url = "http://www.space.com/peopleinterviews/clarke_believe_010227.html"
    fetched = "https://web.archive.org/web/20010413101341/" + url
    article = Article(
        text="Article body with enough words to be a real content region here.",
        title="Clarke's Believe It or Not",
        authors=None,
        date="2001-04-13",  # capture date, scraped from the archive's chrome
        sitename="space.com",
        description="desc",
    )
    staging = _create_wayback_staging(tmp_path, url, fetched)
    output = tmp_path / "output"
    with patch("ingest_webpage.extract_article", return_value=article):
        ingest_webpage.run(staging, output, force=False)
    content = list((output / "store").glob("*.md"))[0].read_text()
    assert "date_published: 2001-02-27" in content
    assert "date_published: 2001-04-13" not in content


def test_wayback_capture_date_kept_when_no_slug_date(tmp_path):
    """With no date recoverable from the URL, the capture date is kept (flagged in
    logs) rather than silently replaced with today - so no regression."""
    url = "http://example.com/story/interview.html"
    fetched = "https://web.archive.org/web/20200101120000/" + url
    article = Article(
        text="Article body with enough words to be a real content region here.",
        title="Interview",
        authors=None,
        date="2020-01-01",  # equals the capture; nothing recoverable from the URL
        sitename="example",
        description="d",
    )
    staging = _create_wayback_staging(tmp_path, url, fetched)
    output = tmp_path / "output"
    with patch("ingest_webpage.extract_article", return_value=article):
        ingest_webpage.run(staging, output, force=False)
    content = list((output / "store").glob("*.md"))[0].read_text()
    assert "date_published: 2020-01-01" in content
