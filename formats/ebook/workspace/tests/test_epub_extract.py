"""Tests for EPUB pagebreak -> printed_page marker extraction."""

from bs4 import BeautifulSoup

from extraction.epub_extract import (
    PAGE_TOKEN_PREFIX,
    PAGE_TOKEN_SUFFIX,
    _collect_pagebreaks,
    _expand_page_tokens,
    _hoist_heading_page_markers,
    _is_pagebreak,
    _pagebreak_label,
)


def _tag(markup: str):
    return BeautifulSoup(markup, "lxml-xml").find("span")


def test_is_pagebreak_epub_type():
    assert _is_pagebreak(
        _tag('<span epub:type="pagebreak" id="page_308" title="308"/>')
    )


def test_is_pagebreak_doc_pagebreak_role():
    assert _is_pagebreak(_tag('<span role="doc-pagebreak" title="42"/>'))


def test_is_not_pagebreak_plain_span():
    assert not _is_pagebreak(_tag('<span class="foo">text</span>'))


def test_pagebreak_label_prefers_title():
    assert (
        _pagebreak_label(_tag('<span epub:type="pagebreak" id="page_5" title="308"/>'))
        == "308"
    )


def test_pagebreak_label_from_id_when_no_title():
    assert _pagebreak_label(_tag('<span epub:type="pagebreak" id="page_15"/>')) == "15"


def test_pagebreak_label_roman_from_id():
    assert (
        _pagebreak_label(_tag('<span epub:type="pagebreak" id="page_viii"/>')) == "viii"
    )


def test_collect_pagebreaks_inserts_token_and_removes_span():
    body = BeautifulSoup(
        '<body><p>Before<span epub:type="pagebreak" title="308"/>After</p></body>',
        "lxml-xml",
    ).find("body")
    _collect_pagebreaks(body)
    assert f"{PAGE_TOKEN_PREFIX}308{PAGE_TOKEN_SUFFIX}" in body.get_text()
    assert not any(_is_pagebreak(t) for t in body.find_all(True))


def test_collect_pagebreaks_drops_unlabelled():
    body = BeautifulSoup(
        '<body><p>X<span epub:type="pagebreak"/>Y</p></body>', "lxml-xml"
    ).find("body")
    _collect_pagebreaks(body)
    assert PAGE_TOKEN_PREFIX not in body.get_text()


def test_expand_page_tokens_arabic():
    md = f"Before\n\n{PAGE_TOKEN_PREFIX}308{PAGE_TOKEN_SUFFIX}\n\nAfter"
    assert "<!-- printed_page: 308 -->" in _expand_page_tokens(md)


def test_expand_page_tokens_roman_and_index_labels():
    assert (
        _expand_page_tokens(f"{PAGE_TOKEN_PREFIX}viii{PAGE_TOKEN_SUFFIX}")
        == "<!-- printed_page: viii -->"
    )
    assert (
        _expand_page_tokens(f"{PAGE_TOKEN_PREFIX}I15{PAGE_TOKEN_SUFFIX}")
        == "<!-- printed_page: I15 -->"
    )


def test_hoist_single_marker_from_heading():
    assert (
        _hoist_heading_page_markers("## <!-- printed_page: 13 --> Chapter 2")
        == "<!-- printed_page: 13 -->\n## Chapter 2"
    )


def test_hoist_multiple_markers_from_heading():
    assert (
        _hoist_heading_page_markers(
            "## <!-- printed_page: vi --> <!-- printed_page: vii --> Dedication"
        )
        == "<!-- printed_page: vi -->\n<!-- printed_page: vii -->\n## Dedication"
    )


def test_hoist_heading_only_marker_drops_empty_heading():
    assert (
        _hoist_heading_page_markers("## <!-- printed_page: 5 -->")
        == "<!-- printed_page: 5 -->"
    )


def test_hoist_leaves_ordinary_heading_untouched():
    assert _hoist_heading_page_markers("## Chapter 2\n\ntext") == "## Chapter 2\n\ntext"
