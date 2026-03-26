from unittest.mock import patch, Mock

from extraction.trafilatura_ext import extract_article, Article


SAMPLE_HTML = """
<html>
<head>
    <meta property="og:title" content="Test Article Title">
    <meta property="article:author" content="Jane Smith">
    <meta property="article:published_time" content="2023-06-05">
    <meta property="og:site_name" content="Test News">
</head>
<body>
<article>
<h1>Test Article Title</h1>
<p>This is the first paragraph of the article with enough text to be extracted.</p>
<p>This is the second paragraph with more content for extraction.</p>
</article>
</body>
</html>
"""


def test_extract_article_returns_article():
    result = extract_article(SAMPLE_HTML, "https://example.com")
    assert result is not None
    assert isinstance(result, Article)
    assert len(result.text) > 0


def test_extract_article_returns_none_for_empty_html():
    result = extract_article("", "https://example.com")
    assert result is None


def test_extract_article_returns_none_for_non_article():
    result = extract_article(
        "<html><body><nav>Menu</nav></body></html>", "https://example.com"
    )
    assert result is None


@patch("extraction.trafilatura_ext.bare_extraction")
def test_extract_article_maps_metadata(mock_extract):
    doc = Mock()
    doc.text = "Article body text"
    doc.title = "Test Title"
    doc.author = "Alice; Bob"
    doc.date = "2023-06-05"
    doc.sitename = "Test Site"
    doc.description = "A test article"
    mock_extract.return_value = doc

    result = extract_article("<html></html>", "https://example.com")
    assert result.title == "Test Title"
    assert result.authors == ["Alice", "Bob"]
    assert result.date == "2023-06-05"
    assert result.sitename == "Test Site"


@patch("extraction.trafilatura_ext.bare_extraction")
def test_extract_article_handles_none_author(mock_extract):
    doc = Mock()
    doc.text = "Article body text"
    doc.title = "Test"
    doc.author = None
    doc.date = "2023-01-01"
    doc.sitename = None
    doc.description = None
    mock_extract.return_value = doc

    result = extract_article("<html></html>", "https://example.com")
    assert result.authors is None


@patch("extraction.trafilatura_ext.bare_extraction")
def test_extract_article_returns_none_when_no_text(mock_extract):
    doc = Mock()
    doc.text = ""
    mock_extract.return_value = doc

    result = extract_article("<html></html>", "https://example.com")
    assert result is None


@patch("extraction.trafilatura_ext.bare_extraction")
def test_extract_article_returns_none_when_bare_extraction_returns_none(mock_extract):
    mock_extract.return_value = None

    result = extract_article("<html></html>", "https://example.com")
    assert result is None
