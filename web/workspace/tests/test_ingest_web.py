import json
from unittest.mock import patch

from extraction.trafilatura_ext import Article

import ingest_web


SAMPLE_ARTICLE = Article(
    text="# Test Article\n\nFirst paragraph of article content.\n\nSecond paragraph.",
    title="Test Article",
    authors=["Jane Smith"],
    date="2023-06-05",
    sitename="Example News",
    description="A test article",
)


def _patch_fetchers(http_html=None, wayback_html=None):
    """Patch the FETCHERS list with controlled return values."""

    def _make_fetcher(html):
        def fetcher(url):
            return html

        return fetcher

    return patch(
        "ingest_web.FETCHERS",
        [
            ("http", _make_fetcher(http_html)),
            ("wayback", _make_fetcher(wayback_html)),
        ],
    )


@patch("ingest_web.extract_article", return_value=SAMPLE_ARTICLE)
def test_ingest_writes_record_to_store(mock_extract, tmp_path):
    with _patch_fetchers(http_html="<html>content</html>"):
        ingest_web.run(
            url="https://example.com/article",
            output_dir=tmp_path,
            force=False,
        )

    md_files = list((tmp_path / "store").glob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text()
    assert "schema: anomalica/record/1" in content
    assert "source_type: web" in content
    assert "source_url: https://example.com/article" in content
    assert "Test Article" in content


@patch("ingest_web.extract_article", return_value=SAMPLE_ARTICLE)
def test_ingest_writes_metadata(mock_extract, tmp_path):
    with _patch_fetchers(http_html="<html>content</html>"):
        ingest_web.run(
            url="https://example.com/article",
            output_dir=tmp_path,
            force=False,
        )

    meta_files = list((tmp_path / "store").glob("*.meta.json"))
    assert len(meta_files) == 1
    meta = json.loads(meta_files[0].read_text())
    assert meta["input_url"] == "https://example.com/article"
    assert meta["fetch_method"] == "http"
    assert "duration_ms" in meta
    assert "trafilatura_metadata" in meta


@patch("ingest_web.extract_article", return_value=SAMPLE_ARTICLE)
def test_ingest_creates_symlink(mock_extract, tmp_path):
    with _patch_fetchers(http_html="<html>content</html>"):
        ingest_web.run(
            url="https://example.com/article",
            output_dir=tmp_path,
            force=False,
        )

    links = list((tmp_path / "records").glob("*.md"))
    assert len(links) == 1
    assert links[0].is_symlink()
    assert "2023-06-05-web-test-article" in links[0].name


@patch("ingest_web.extract_article", return_value=SAMPLE_ARTICLE)
def test_ingest_skips_when_exists(mock_extract, tmp_path):
    with _patch_fetchers(http_html="<html>content</html>"):
        ingest_web.run(
            url="https://example.com/article", output_dir=tmp_path, force=False
        )
        ingest_web.run(
            url="https://example.com/article", output_dir=tmp_path, force=False
        )

    md_files = list((tmp_path / "store").glob("*.md"))
    assert len(md_files) == 1


@patch("ingest_web.extract_article", return_value=SAMPLE_ARTICLE)
def test_ingest_re_extracts_with_force(mock_extract, tmp_path):
    with _patch_fetchers(http_html="<html>content</html>"):
        ingest_web.run(
            url="https://example.com/article", output_dir=tmp_path, force=False
        )
        ingest_web.run(
            url="https://example.com/article", output_dir=tmp_path, force=True
        )

    assert mock_extract.call_count == 2


def test_ingest_exits_when_all_fetchers_fail(tmp_path):
    with _patch_fetchers(http_html=None, wayback_html=None):
        exit_code = ingest_web.run(
            url="https://example.com/article",
            output_dir=tmp_path,
            force=False,
        )
    assert exit_code != 0


@patch("ingest_web.extract_article", return_value=None)
def test_ingest_exits_when_extraction_fails_all_fetchers(mock_extract, tmp_path):
    """HTTP returns HTML but extraction fails, then wayback also returns HTML but extraction fails."""
    with _patch_fetchers(
        http_html="<html>paywall</html>", wayback_html="<html>also bad</html>"
    ):
        exit_code = ingest_web.run(
            url="https://example.com/article",
            output_dir=tmp_path,
            force=False,
        )
    assert exit_code != 0
    # Both fetchers were tried
    assert mock_extract.call_count == 2


def test_ingest_falls_back_to_wayback_when_http_extraction_fails(tmp_path):
    """HTTP returns HTML but extraction fails. Wayback returns HTML and extraction succeeds."""
    call_count = {"n": 0}

    def _extract_side_effect(html, url):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return None  # HTTP HTML is a paywall
        return SAMPLE_ARTICLE  # Wayback HTML works

    with _patch_fetchers(
        http_html="<html>paywall</html>", wayback_html="<html>article</html>"
    ):
        with patch("ingest_web.extract_article", side_effect=_extract_side_effect):
            exit_code = ingest_web.run(
                url="https://example.com/article",
                output_dir=tmp_path,
                force=False,
            )

    assert exit_code == 0
    meta_files = list((tmp_path / "store").glob("*.meta.json"))
    meta = json.loads(meta_files[0].read_text())
    assert meta["fetch_method"] == "wayback"
