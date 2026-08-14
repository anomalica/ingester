"""Tests for EPUB pagebreak -> printed_page marker extraction."""

from bs4 import BeautifulSoup

from extraction.epub_extract import (
    PAGE_TOKEN_PREFIX,
    PAGE_TOKEN_SUFFIX,
    _collect_pagebreaks,
    _expand_page_tokens,
    _hoist_heading_page_markers,
    _is_chapter_number,
    _is_pagebreak,
    _pagebreak_label,
    _split_enumerator,
    _title_and_number,
)


def _tag(markup: str):
    return BeautifulSoup(markup, "lxml-xml").find("span")


def _body(markup: str):
    return BeautifulSoup(markup, "lxml-xml").find("body")


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


def test_is_chapter_number():
    assert _is_chapter_number("3")
    assert _is_chapter_number("3.")
    assert _is_chapter_number("14")
    assert _is_chapter_number("Chapter 3")
    assert not _is_chapter_number("In the Field")
    assert not _is_chapter_number("James")
    assert not _is_chapter_number("The Material Code")


def test_title_and_number_number_paragraph_above_heading():
    # the common case: a number styled as its own <p> above the title heading
    # (as in '<div><p>1</p><h1>The Secrecy</h1></div>')
    body = _body("<body><div><p>1</p><h1>The Secrecy</h1></div><p>text</p></body>")
    title, number, node = _title_and_number(body)
    assert (title, number) == ("The Secrecy", "1")
    assert node is not None and node.get_text(strip=True) == "1"


def test_title_and_number_number_heading_above_heading():
    # a numbered chapter that opens with the number as a heading, then the title
    body = _body("<body><h2>3</h2><h2>In the Field</h2><p>text</p></body>")
    title, number, node = _title_and_number(body)
    assert (title, number) == ("In the Field", "3")
    assert node is not None


def test_title_and_number_part_divider_has_no_number():
    # 'PART ONE' above the title is not a number - a part carries no chapter number
    body = _body("<body><p>PART ONE</p><h1>Bodies and Powers</h1><p>text</p></body>")
    title, number, node = _title_and_number(body)
    assert (title, number, node) == ("Bodies and Powers", None, None)


def test_title_and_number_plain_title():
    body = _body("<body><h2>Preface</h2><p>text</p></body>")
    title, number, node = _title_and_number(body)
    assert (title, number, node) == ("Preface", None, None)


def test_title_and_number_only_number_keeps_it_as_title():
    # a chapter whose only heading is a number keeps the number (no title exists)
    body = _body("<body><h1>7</h1><p>text</p></body>")
    title, number, node = _title_and_number(body)
    assert title == "7"


def test_split_enumerator_arabic():
    assert _split_enumerator("1. The Secrecy") == ("1", "The Secrecy")


def test_split_enumerator_roman_part():
    assert _split_enumerator("II. Finding Our Liberty") == ("II", "Finding Our Liberty")


def test_split_enumerator_plain_title_unchanged():
    assert _split_enumerator("Introduction: Danger and Promise") == (
        None,
        "Introduction: Danger and Promise",
    )
    # a lower-case word that merely looks Roman is not an enumerator
    assert _split_enumerator("Mix. A Memoir") == (None, "Mix. A Memoir")
