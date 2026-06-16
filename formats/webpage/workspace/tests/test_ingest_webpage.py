import json
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
    links = list((output / "records").glob("*.md"))
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
