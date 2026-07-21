from unittest.mock import patch, Mock

from extraction.trafilatura_ext import extract_article, strip_chrome, Article


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


def test_strip_chrome_removes_furniture_keeps_article():
    html = (
        "<html><body><article>"
        "<p>Real article prose that must survive the strip intact.</p>"
        '<div class="latest-stories"><h2>Latest Stories</h2>'
        '<a href="/x">Some other article teaser</a></div>'
        '<aside class="newsletter-signup">Sign up for our newsletter</aside>'
        "</article></body></html>"
    )
    out = strip_chrome(html)
    assert "Real article prose" in out
    assert "Latest Stories" not in out
    assert "newsletter-signup" not in out


def test_strip_chrome_noop_on_clean_html():
    html = "<html><body><article><p>Clean article, no furniture.</p></article></body></html>"
    assert strip_chrome(html) == html


def test_strip_chrome_returns_string_on_empty_input():
    assert isinstance(strip_chrome(""), str)


def test_extract_article_excludes_furniture_from_body():
    html = (
        '<html><head><meta property="og:title" content="Headline"></head>'
        "<body><article><h1>Headline</h1>"
        "<p>This is the substantive article body with plenty of words to extract here.</p>"
        "<p>A second real paragraph continuing the article with additional detail and more.</p>"
        '<div class="latest-stories"><h2>Latest Stories</h2>'
        "<p>Unrelated recirculation teaser that must not land in the body.</p></div>"
        "</article></body></html>"
    )
    art = extract_article(html, "https://example.com")
    assert art is not None
    assert "substantive article body" in art.text
    assert "Latest Stories" not in art.text
    assert "recirculation teaser" not in art.text


@patch("extraction.trafilatura_ext.bare_extraction")
def test_extract_article_strips_leading_title_heading(mock_extract):
    # trafilatura leads the body with the page title as a heading; since the
    # title lives in frontmatter only, that duplicate heading is dropped.
    doc = Mock()
    doc.text = "# Rep. Burlison Welcomes David Grusch\n\nWashington, D.C. - the body."
    doc.title = "Rep. Burlison Welcomes David Grusch"
    doc.author = None
    doc.date = None
    doc.sitename = None
    doc.description = None
    mock_extract.return_value = doc

    result = extract_article("<html></html>", "https://example.com")
    assert not result.text.lstrip().startswith("#")
    assert "Washington, D.C. - the body." in result.text
    assert result.title == "Rep. Burlison Welcomes David Grusch"


@patch("extraction.trafilatura_ext.bare_extraction")
def test_extract_article_keeps_non_title_leading_heading(mock_extract):
    # a genuine first heading that is NOT the title is left untouched.
    doc = Mock()
    doc.text = "# Section One\n\nSome content here for the body."
    doc.title = "A Completely Different Title"
    doc.author = None
    doc.date = None
    doc.sitename = None
    doc.description = None
    mock_extract.return_value = doc

    result = extract_article("<html></html>", "https://example.com")
    assert result.text.lstrip().startswith("# Section One")
