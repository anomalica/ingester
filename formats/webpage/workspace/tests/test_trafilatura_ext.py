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


EMPHASIS_HTML = """
<html>
<head>
    <meta property="og:title" content="Late Officer Linked To Program">
    <meta property="og:site_name" content="Liberation Times | Reimagining Old News">
</head>
<body>
<article>
<h1>Late Officer Linked To Program</h1>
<p>Burlison <strong>has said</strong> he has <em>“grave concerns”</em> that the death appears <b>“suspicious”</b>, suggesting the officer may have been silenced before he could reveal what he knew.</p>
<p><strong>“There are not many people you can share that with,”</strong> he said. <strong>“But those who carry that weight know it and understand it.”</strong></p>
<h2>Background to the case</h2>
<p>He served at a number of high-level institutions and deployed twice, and his obituary states that he earned a decoration for valour during the campaign.</p>
</article>
</body>
</html>
"""


def test_extract_article_carries_no_emphasis_markers():
    result = extract_article(EMPHASIS_HTML, "https://www.liberationtimes.com/home/x")
    assert "*" not in result.text
    assert "_" not in result.text


def test_extract_article_keeps_prose_order_and_spacing_around_emphasis():
    result = extract_article(EMPHASIS_HTML, "https://www.liberationtimes.com/home/x")
    assert (
        "Burlison has said he has “grave concerns” that the death appears "
        "“suspicious”, suggesting the officer" in result.text
    )
    assert (
        "“There are not many people you can share that with,” he said. "
        "“But those who carry that weight know it and understand it.”" in result.text
    )


def test_extract_article_keeps_headings_and_paragraph_breaks():
    result = extract_article(EMPHASIS_HTML, "https://www.liberationtimes.com/home/x")
    assert "## Background to the case" in result.text
    paragraphs = [p for p in result.text.split("\n\n") if p.strip()]
    assert len(paragraphs) >= 4


def test_extract_article_drops_title_heading_carrying_site_suffix():
    # The page title arrives with the site's chrome appended; the body's first
    # heading is the bare title. Both name the same thing - the heading goes.
    html = EMPHASIS_HTML.replace(
        'content="Late Officer Linked To Program"',
        'content="Late Officer Linked To Program — Liberation Times | Reimagining Old News"',
    )
    result = extract_article(html, "https://www.liberationtimes.com/home/x")
    assert not result.text.lstrip().startswith("# Late Officer")


def test_unwrap_emphasis_keeps_text_and_tails():
    from extraction.trafilatura_ext import unwrap_emphasis

    out = unwrap_emphasis("<p>He <strong>said</strong> hi <em>there</em>.</p>")
    assert "<strong>" not in out and "<em>" not in out
    assert "He said hi there." in out


def test_strip_chrome_removes_a_recirculation_widget():
    html = """<html><body><article>
    <p>Article prose that stays, long enough to be extracted properly.</p>
    <section class="mrf-irc" data-mrf-recirculation="inline-recirc-breaker" aria-label="Recommended Stories">
    <p class="mrf-irc__title">Recommended Stories</p>
    <ul><li><a href="/x"><img src="https://cdn.example/thumb.jpg" width="300" height="200">A story</a></li></ul>
    </section>
    <p>More article prose after the widget, also long enough.</p>
    </article></body></html>"""
    out = strip_chrome(html)
    assert "Recommended Stories" not in out and "thumb.jpg" not in out
    assert "Article prose that stays" in out and "More article prose" in out
