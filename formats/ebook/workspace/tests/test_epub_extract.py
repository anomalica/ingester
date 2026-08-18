"""Tests for EPUB pagebreak -> printed_page marker extraction."""

from bs4 import BeautifulSoup

from extraction.epub_extract import (
    FN_TOKEN_PREFIX,
    FN_TOKEN_SUFFIX,
    PAGE_TOKEN_PREFIX,
    PAGE_TOKEN_SUFFIX,
    _analyse_body,
    _collect_footnotes,
    _collect_pagebreaks,
    _enum_to_int,
    _expand_footnote_tokens,
    _is_chapter_number,
    _is_noteref,
    _is_pagebreak,
    _note_content,
    _pagebreak_label,
    _parse_designation,
    _expand_page_tokens,
    _hoist_heading_page_markers,
)


class _StubResolver:
    """Stands in for _FootnoteResolver in tests: numbers refs in order and
    returns a fixed definition for each."""

    def __init__(self) -> None:
        self.count = 0

    def resolve(self, base_file: str, href: str) -> tuple[str, str]:
        self.count += 1
        return str(self.count), f"note {self.count}"


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


def test_enum_to_int():
    assert _enum_to_int("7") == 7
    assert _enum_to_int("IV") == 4
    assert _enum_to_int("XVI") == 16
    assert _enum_to_int("One") == 1
    assert _enum_to_int("Twelve") == 12
    assert _enum_to_int("twenty-one") == 21
    assert _enum_to_int("Notes") is None


def test_parse_designation_arabic():
    assert _parse_designation("1. The Secrecy") == ("1", "The Secrecy", False)


def test_parse_designation_roman_part():
    assert _parse_designation("II. Finding Our Liberty") == (
        None,
        "Finding Our Liberty",
        True,
    )


def test_parse_designation_chapter_word_with_title():
    assert _parse_designation("Chapter One: Journey to Fatima") == (
        "1",
        "Journey to Fatima",
        False,
    )
    assert _parse_designation("Chapter Twelve - The Monuments of Mars") == (
        "12",
        "The Monuments of Mars",
        False,
    )


def test_parse_designation_chapter_roman_no_title():
    assert _parse_designation("Chapter IV") == ("4", None, False)


def test_parse_designation_part_word():
    assert _parse_designation("Part One") == (None, None, True)


def test_parse_designation_plain_title_unchanged():
    assert _parse_designation("Introduction: Danger and Promise") == (
        None,
        "Introduction: Danger and Promise",
        False,
    )
    assert _parse_designation("Contents") == (None, "Contents", False)
    # a word that merely looks Roman is not an enumerator
    assert _parse_designation("Mix. A Memoir") == (None, "Mix. A Memoir", False)


def test_parse_designation_bare_number_and_ceiling():
    assert _parse_designation("7") == ("7", None, False)
    assert _parse_designation("ONE") == ("1", None, False)
    # a stray page/endnote number above the ceiling is not a chapter
    assert _parse_designation("178") == (None, "178", False)


def test_analyse_body_number_paragraph_above_heading():
    # the common case: a number styled as its own <p> above the title heading
    body = _body("<body><div><p>1</p><h1>The Secrecy</h1></div><p>text</p></body>")
    title, number, node = _analyse_body(body)
    assert (title, number) == ("The Secrecy", "1")
    assert node is not None and node.get_text(strip=True) == "1"


def test_analyse_body_chapter_word_heading_then_title():
    # 'Chapter One' as its own heading, the real title in the next heading
    body = _body(
        "<body><h2>Chapter One</h2><h2>The Right State of Mind</h2><p>x</p></body>"
    )
    title, number, node = _analyse_body(body)
    assert (title, number) == ("The Right State of Mind", "1")
    assert node is not None


def test_analyse_body_part_divider_has_no_number():
    body = _body("<body><p>PART ONE</p><h1>Bodies and Powers</h1><p>text</p></body>")
    title, number, node = _analyse_body(body)
    assert (title, number, node) == ("Bodies and Powers", None, None)


def test_analyse_body_plain_title():
    body = _body("<body><h2>Preface</h2><p>text</p></body>")
    title, number, node = _analyse_body(body)
    assert (title, number, node) == ("Preface", None, None)


def test_analyse_body_bare_cardinal_is_number_not_title():
    # a chapter whose only heading is a spelled-out number ('ONE') has no title
    body = _body("<body><h1>ONE</h1><p>text</p></body>")
    title, number, node = _analyse_body(body)
    assert (title, number) == (None, "1")
    assert node is not None


def test_is_noteref_sup_link():
    a = _body('<body><a href="notes.xhtml#n5"><sup>3</sup></a></body>').find("a")
    assert _is_noteref(a)


def test_is_noteref_epub_type():
    a = _body('<body><a epub:type="noteref" href="#n5">3</a></body>').find("a")
    assert _is_noteref(a)


def test_is_noteref_rejects_plain_and_external_links():
    plain = _body('<body><a href="chapter2.xhtml">Chapter 2</a></body>').find("a")
    external = _body('<body><a href="https://x.com"><sup>1</sup></a></body>').find("a")
    assert not _is_noteref(plain)
    assert not _is_noteref(external)


def test_note_content_strips_marker_and_backlink():
    note = _body(
        '<body><li id="n1">1. Smith 1967, p. 34. '
        '<a href="ch.xhtml#r1">↩</a></li></body>'
    ).find("li")
    assert _note_content(note) == "Smith 1967, p. 34."


def test_note_content_keeps_external_link():
    note = _body(
        '<body><p id="n2">fn2 See <a href="https://x.com">the site</a>.</p></body>'
    ).find("p")
    assert _note_content(note) == "See [the site](https://x.com)."


def test_collect_footnotes_replaces_ref_with_marker():
    body = _body(
        '<body><p>A claim.<a href="notes.xhtml#n1"><sup>1</sup></a> More.</p></body>'
    )
    defs = _collect_footnotes(body, "ch.xhtml", _StubResolver())
    assert defs == ["[^1]: note 1"]
    token = f"{FN_TOKEN_PREFIX}1{FN_TOKEN_SUFFIX}"
    assert token in str(body)
    assert body.find("a") is None


def test_expand_footnote_tokens():
    md = f"A claim.{FN_TOKEN_PREFIX}3{FN_TOKEN_SUFFIX} More."
    assert _expand_footnote_tokens(md) == "A claim.[^3] More."


def _minimal_epub(path: str) -> str:
    """A two-section EPUB: a numbered chapter and a back-matter section, so a
    test can prove the book title is not overwritten by the last section."""
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_title("My Book")
    book.add_author("A. Writer")
    ch = epub.EpubHtml(title="1. First Chapter", file_name="c1.xhtml")
    ch.content = "<html><body><h1>1. First Chapter</h1><p>Body one.</p></body></html>"
    back = epub.EpubHtml(title="About the Author", file_name="c2.xhtml")
    back.content = "<html><body><h1>About the Author</h1><p>A bio.</p></body></html>"
    book.add_item(ch)
    book.add_item(back)
    book.toc = (ch, back)
    book.spine = [ch, back]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(path, book)
    return path


def test_extract_book_title_survives_the_chapter_loop(tmp_path):
    from extraction.epub_extract import extract

    book = extract(_minimal_epub(str(tmp_path / "b.epub")))
    # the book keeps its own title, not the last section's ("About the Author")
    assert book.title == "My Book"
    numbered = [c for c in book.chapters if c.number]
    assert numbered and numbered[0].number == "1"


# --- drop-cap rejoin -----------------------------------------------------------

from extraction.epub_extract import rejoin_dropcaps  # noqa: E402


def test_rejoin_dropcaps_joins_unambiguous_capitals():
    # A decorative drop cap split onto its own line rejoins with no space.
    assert rejoin_dropcaps("W\nhile exploring") == "While exploring"
    assert rejoin_dropcaps("# Intro\n\nT\nhe cases") == "# Intro\n\nThe cases"
    assert rejoin_dropcaps("P\nroponents say") == "Proponents say"


def test_rejoin_dropcaps_leaves_A_and_I_for_the_inspection_layer():
    # 'I'/'A' may be a whole word ('I was', 'A study') or a first letter ('In',
    # 'After', 'Indridi') - a regex cannot tell, so it must not guess and produce
    # 'Iwas'. These are deferred, not joined here.
    assert rejoin_dropcaps("I\nwas afraid") == "I\nwas afraid"
    assert rejoin_dropcaps("A\nfter the war") == "A\nfter the war"


def test_rejoin_dropcaps_ignores_non_splits():
    # A capital followed by an uppercase run (a byline) or a full line is untouched.
    assert rejoin_dropcaps("L\nESLIE KEAN") == "L\nESLIE KEAN"
    assert rejoin_dropcaps("The cat sat") == "The cat sat"
