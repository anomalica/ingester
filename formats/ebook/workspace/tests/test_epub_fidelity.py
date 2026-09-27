"""Offline, source-to-Ingest regressions for generic EPUB interpretation.

The ZIP fixtures use standard EPUB markup, not exporter-specific attributes.
They deliberately avoid ebooklib's writer normalising the source under test.
"""

import base64
import hashlib
import json
import re
from xml.sax.saxutils import escape, quoteattr
from zipfile import ZipFile

import pytest
import yaml

from extraction.epub_extract import EpubExtractionError, EpubExtractionWarning, extract
from ingest_ebook import _render_body, run
from pipeline_version import current_version
from record3 import finalise_handler_record, read_record
from validator import validate


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
)
XHTML = (
    '<html xmlns="http://www.w3.org/1999/xhtml" '
    'xmlns:epub="http://www.idpf.org/2007/ops" '
    'xmlns:xlink="http://www.w3.org/1999/xlink"><head><title>Fixture</title></head>'
    "<body>{}</body></html>"
)


def _epub(
    tmp_path, documents, *, toc="", pages="", media=None, ncx=None, nav_path="nav.xhtml"
):
    media = media or {}
    manifest = [
        f'<item id="nav" href={quoteattr(nav_path)} media-type="application/xhtml+xml" properties="nav"/>'
    ]
    manifest += [
        f'<item id="d{i}" href={quoteattr(name)} media-type="application/xhtml+xml"/>'
        for i, (name, _) in enumerate(documents)
    ]
    manifest += [
        f'<item id="m{i}" href={quoteattr(name)} media-type={quoteattr(mime)}/>'
        for i, (name, (mime, _)) in enumerate(media.items())
    ]
    if ncx:
        manifest.append(
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        )
    spine = "".join(f'<itemref idref="d{i}"/>' for i in range(len(documents)))
    package = (
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="id">urn:uuid:10000000-0000-0000-0000-000000000001</dc:identifier>'
        "<dc:title>Fidelity fixture</dc:title><dc:language>en</dc:language>"
        "<dc:date>2020-08</dc:date></metadata><manifest>"
        + "".join(manifest)
        + f"</manifest><spine>{spine}</spine></package>"
    )
    path = tmp_path / "fixture.epub"
    with ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OPS/package.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        archive.writestr("OPS/package.opf", package)
        archive.writestr(
            "OPS/" + nav_path,
            XHTML.format(
                '<nav epub:type="toc"><ol>'
                + toc
                + '</ol></nav><nav epub:type="page-list"><ol>'
                + pages
                + "</ol></nav>"
            ),
        )
        for name, body in documents:
            archive.writestr("OPS/" + name, XHTML.format(body))
        for name, (_, data) in media.items():
            archive.writestr("OPS/" + name, data)
        if ncx:
            archive.writestr("OPS/toc.ncx", ncx)
    return path


def _link(href, text):
    return f"<li><a href={quoteattr(href)}>{escape(text)}</a></li>"


def _annotations(body, key):
    values = []
    for match in re.finditer(r"<!--(.*?)-->", body, re.S):
        value = yaml.safe_load(match[1])
        if isinstance(value, dict) and key in value:
            values.append(value[key])
    return values


@pytest.mark.parametrize(
    "separator", ["<em> </em>", "<strong> </strong>", "<em><b> </b></em>"]
)
def test_emphasised_word_separator_survives_markdown_conversion(tmp_path, separator):
    path = _epub(
        tmp_path,
        [("one.xhtml", f"<p>It is so{separator}<em>quiet</em> here.</p>")],
    )
    body = _render_body(extract(str(path)))
    assert "so *quiet* here." in body
    assert "so*quiet*" not in body


def test_repeated_image_bytes_have_independent_alt_and_printed_captions(tmp_path):
    path = _epub(
        tmp_path,
        [
            (
                "Text/one.xhtml",
                (
                    '<p>Before.</p><figure><img src="../Images/one.png" alt="First use"/>'
                    '<figcaption>Figure 1. A <em>printed</em> caption.<br/>Credit "A" --&gt; B.</figcaption></figure>'
                    '<p>Between.</p><figure><img src="../Images/one.png" alt="Second use"/>'
                    "<figcaption>Second caption.</figcaption></figure><p>After.</p>"
                ),
            )
        ],
        media={"Images/one.png": ("image/png", PNG)},
    )
    book = extract(str(path))
    body = _render_body(book)
    images = _annotations(body, "image")
    filename = hashlib.sha256(PNG).hexdigest()[:12] + ".png"
    assert images == [
        {
            "file": filename,
            "alt": "First use",
            "caption": 'Figure 1. A printed caption.\nCredit "A" --> B.',
        },
        {"file": filename, "alt": "Second use", "caption": "Second caption."},
    ]
    assert len(book.images) == 1
    assert book.images[0].bytes == PNG
    assert (
        body.index("Before.")
        < body.index("First use")
        < body.index("Between.")
        < body.index("Second use")
        < body.index("After.")
    )
    assert body.count("Second caption.") == 1
    assert "ANOMALICAIMG" not in body


def test_multi_image_caption_is_retained_once_without_guessing_an_owner(tmp_path):
    path = _epub(
        tmp_path,
        [
            (
                "one.xhtml",
                '<figure><img src="one.png"/><img src="one.png"/><figcaption>Both panels.</figcaption></figure>',
            )
        ],
        media={"one.png": ("image/png", PNG)},
    )
    with pytest.warns(EpubExtractionWarning, match="Caption kept as prose"):
        body = _render_body(extract(str(path)))
    assert len(_annotations(body, "image")) == 2
    assert all("caption" not in image for image in _annotations(body, "image"))
    assert body.count("Both panels.") == 1


def test_adjacent_prose_is_not_invented_as_a_caption(tmp_path):
    path = _epub(
        tmp_path,
        [
            (
                "one.xhtml",
                '<img src="one.png" alt="Accessibility text"/><p>A nearby paragraph.</p>',
            )
        ],
        media={"one.png": ("image/png", PNG)},
    )
    body = _render_body(extract(str(path)))
    assert "caption" not in _annotations(body, "image")[0]
    assert "A nearby paragraph." in body


def test_caption_containing_a_page_point_stays_at_its_source_position(tmp_path):
    path = _epub(
        tmp_path,
        [
            (
                "one.xhtml",
                '<figure><img src="one.png"/><figcaption>photo<span epub:type="pagebreak" title="15"/>graph</figcaption></figure>',
            )
        ],
        media={"one.png": ("image/png", PNG)},
    )
    with pytest.warns(EpubExtractionWarning, match="Caption kept as prose"):
        body = _render_body(extract(str(path)))
    assert "photo<!-- printed_page: 15 -->graph" in body
    assert "caption" not in _annotations(body, "image")[0]


@pytest.mark.parametrize(
    "markup",
    [
        '<img src="missing.png" alt="Source evidence"/>',
        '<img alt="Source evidence"/>',
        '<img src="https://example.test/image.png" alt="Source evidence"/>',
        '<svg><rect width="5" height="5"/></svg>',
        '<object type="image/svg+xml" data="diagram.svg">Fallback text.</object>',
    ],
)
def test_unresolved_or_unrepresentable_media_fails_before_any_record_is_written(
    tmp_path, markup, capsys
):
    source = _epub(
        tmp_path, [("one.xhtml", "<p>Before.</p>" + markup + "<p>After.</p>")]
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "asset": source.name,
                "source": source.name,
                "fetched_at": "2026-09-27T00:00:00Z",
            }
        )
    )
    output = tmp_path / "output"
    assert run(tmp_path, output, force=False) == 1
    assert "EPUB extraction failed:" in capsys.readouterr().err
    assert not output.exists()


def test_svg_image_wrapper_and_svg_file_preserve_referenced_bytes(tmp_path):
    vector = b'<svg xmlns="http://www.w3.org/2000/svg"><text>Drawing</text></svg>'
    path = _epub(
        tmp_path,
        [
            (
                "one.xhtml",
                '<svg xmlns="http://www.w3.org/2000/svg"><image xlink:href="one.png"/></svg><img src="vector.svg"/>',
            )
        ],
        media={"one.png": ("image/png", PNG), "vector.svg": ("image/svg+xml", vector)},
    )
    book = extract(str(path))
    assert [image.bytes for image in book.images] == [PNG, vector]
    assert len(_annotations(_render_body(book), "image")) == 2


@pytest.mark.parametrize(
    "label",
    [
        "15",
        "015",
        "A-15",
        "viii",
        "true",
        "null",
        'A: "quoted" \\ label',
        "紙十五",
        "①",
        "x --> y",
    ],
)
@pytest.mark.parametrize("tag,attribute", [("span", "title"), ("a", "aria-label")])
def test_page_labels_round_trip_and_do_not_split_words(tmp_path, label, tag, attribute):
    markup = f'<p>photo<{tag} role="doc-pagebreak" id="pb" {attribute}={quoteattr(label)}/>graph</p>'
    path = _epub(tmp_path, [("one.xhtml", markup)])
    body = _render_body(extract(str(path)))
    labels = _annotations(body, "printed_page")
    assert len(labels) == 1 and str(labels[0]) == label
    assert re.sub(r"<!--.*?-->", "", body, flags=re.S).strip() == "photograph"
    assert "ANOMALICAPAGE" not in body


def test_unlabelled_page_id_is_not_guessed_from_a_roman_looking_suffix(tmp_path):
    path = _epub(
        tmp_path,
        [
            (
                "one.xhtml",
                '<p>Before<span epub:type="pagebreak" id="unlabelled"/>After</p>',
            )
        ],
    )
    with pytest.warns(EpubExtractionWarning, match="Unlabelled"):
        body = _render_body(extract(str(path)))
    assert not _annotations(body, "printed_page")
    assert "BeforeAfter" in body


def test_visible_page_number_does_not_contaminate_chapter_title(tmp_path):
    path = _epub(
        tmp_path,
        [
            (
                "one.xhtml",
                '<h1><span epub:type="pagebreak" title="15">15</span>First Chapter</h1><p>Body.</p>',
            )
        ],
    )
    book = extract(str(path))
    assert book.chapters[0].title == "First Chapter"
    assert "<!-- printed_page: 15 -->\n# First Chapter" in _render_body(book)


def test_midword_page_marker_does_not_split_chapter_title_metadata(tmp_path):
    path = _epub(
        tmp_path,
        [
            (
                "one.xhtml",
                '<h1>Photo<span epub:type="pagebreak" title="15"/>graph</h1><p>Body.</p>',
            )
        ],
    )
    book = extract(str(path))
    assert book.chapters[0].title == "Photograph"
    assert "# Photo<!-- printed_page: 15 -->graph" in _render_body(book)


def test_page_list_targets_preserve_anchor_adjacency_and_target_prose(tmp_path):
    path = _epub(
        tmp_path,
        [
            (
                "Text/one.xhtml",
                '<p>photo<a id="p15"/>graph</p><p id="p16">Do not delete this paragraph.</p>',
            )
        ],
        pages=_link("Text/one.xhtml#p15", "015") + _link("Text/one.xhtml#p16", "A-16"),
    )
    body = _render_body(extract(str(path)))
    assert _annotations(body, "printed_page") == ["015", "A-16"]
    assert 'photo<!-- printed_page: "015" -->graph' in body
    assert '<!-- printed_page: "A-16" -->Do not delete this paragraph.' in body


def test_ncx_page_target_is_interpreted_without_epub3_marker_attributes(tmp_path):
    ncx = '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head/><docTitle><text>Fixture</text></docTitle><navMap/><pageList><pageTarget id="pg" value="15" type="normal"><navLabel><text>xv</text></navLabel><content src="one.xhtml#p15"/></pageTarget></pageList></ncx>'
    path = _epub(tmp_path, [("one.xhtml", '<p>photo<a id="p15"/>graph</p>')], ncx=ncx)
    body = _render_body(extract(str(path)))
    assert "photo<!-- printed_page: xv -->graph" in body


def test_missing_page_list_target_is_an_explicit_failure(tmp_path):
    path = _epub(
        tmp_path,
        [("one.xhtml", "<p>Body.</p>")],
        pages=_link("one.xhtml#missing", "15"),
    )
    with pytest.raises(EpubExtractionError, match="Missing EPUB page-list targets"):
        extract(str(path))


def test_page_list_can_point_at_an_image_without_losing_the_page_marker(tmp_path):
    path = _epub(
        tmp_path,
        [("one.xhtml", '<img id="p15" src="one.png"/><p>After.</p>')],
        pages=_link("one.xhtml#p15", "15"),
        media={"one.png": ("image/png", PNG)},
    )
    body = _render_body(extract(str(path)))
    assert _annotations(body, "printed_page") == [15]
    assert body.index("printed_page:") < body.index("image:") < body.index("After.")


def test_repeated_pagination_does_not_insert_spaces_at_an_inline_transition(tmp_path):
    source = '<p>start<span epub:type="pagebreak" title="1"/>one<span epub:type="pagebreak" title="2"/>two<span epub:type="pagebreak" title="1"/>again</p>'
    path = _epub(tmp_path, [("one.xhtml", source)])
    body = _render_body(extract(str(path)))
    assert "two<!-- printed_page_sequence: 2 --><!-- printed_page: 1 -->again" in body
    assert re.sub(r"<!--.*?-->", "", body, flags=re.S).strip() == "startonetwoagain"


def test_nested_toc_fragments_create_chapters_not_subsection_chapters(tmp_path):
    toc = (
        '<li><a href="one.xhtml#part">Part One</a><ol><li><a href="one.xhtml#first">1. First</a><ol>'
        + _link("one.xhtml#detail", "Details")
        + "</ol></li>"
        + _link("one.xhtml#second", "2. Second")
        + "</ol></li>"
    )
    source = '<section id="part"><h1>Part One</h1><section id="first"><h1>Chapter 1: First</h1><p>First prose.</p><h2 id="detail">Details</h2><p>Details prose.</p></section><section id="second"><h1>Chapter 2: Second</h1><p>Second prose.</p></section></section>'
    path = _epub(tmp_path, [("one.xhtml", source)], toc=toc)
    book = extract(str(path))
    assert [(c.number, c.title) for c in book.chapters] == [
        (None, "Part One"),
        ("1", "First"),
        ("2", "Second"),
    ]
    body = _render_body(book)
    assert (
        body.count("First prose.")
        == body.count("Details prose.")
        == body.count("Second prose.")
        == 1
    )
    assert "## Details" in body
    assert _annotations(body, "chapter") == [1, 2]


def test_toc_paths_with_same_basename_and_encoded_paths_do_not_collide(tmp_path):
    docs = [
        ("Text A/ch.xhtml", "<h1>1. First</h1><p>First prose.</p>"),
        ("Other/ch.xhtml", "<h1>2. Second</h1><p>Second prose.</p>"),
    ]
    path = _epub(
        tmp_path,
        docs,
        toc=_link("../Text%20A/ch.xhtml", "1. First")
        + _link("../Other/ch.xhtml", "2. Second"),
        nav_path="Nav/nav.xhtml",
    )
    book = extract(str(path))
    assert [(c.number, c.title) for c in book.chapters] == [
        ("1", "First"),
        ("2", "Second"),
    ]


def test_nested_subsection_in_its_own_file_does_not_invent_a_chapter(tmp_path):
    toc = (
        '<li><a href="one.xhtml">1. First</a><ol>'
        + _link("detail.xhtml", "Details")
        + "</ol></li>"
    )
    path = _epub(
        tmp_path,
        [
            ("one.xhtml", "<h1>1. First</h1><p>Body.</p>"),
            ("detail.xhtml", "<h2>Details</h2><p>More.</p>"),
        ],
        toc=toc,
    )
    body = _render_body(extract(str(path)))
    assert _annotations(body, "chapter") == [1]
    assert _annotations(body, "chapter_title") == ["First"]
    assert "## Details" in body and "More." in body


def test_explicit_chapter_headings_share_a_file_without_a_toc(tmp_path):
    path = _epub(
        tmp_path,
        [
            (
                "one.xhtml",
                "<p>Opening prose.</p><h1>Chapter 1: First</h1><p>First prose.</p><h1>Chapter 2: Second</h1><p>Second prose.</p>",
            )
        ],
    )
    body = _render_body(extract(str(path)))
    assert _annotations(body, "chapter") == [1, 2]
    assert (
        body.index("Opening prose.")
        < body.index("<!-- chapter: 1 -->")
        < body.index("First prose.")
        < body.index("<!-- chapter: 2 -->")
    )


def test_printed_number_is_not_suppressed_by_an_unnumbered_toc_title(tmp_path):
    path = _epub(
        tmp_path,
        [("one.xhtml", "<h1>Chapter 1: First Chapter</h1><p>Body.</p>")],
        toc=_link("one.xhtml", "First Chapter"),
    )
    assert [(c.number, c.title) for c in extract(str(path)).chapters] == [
        ("1", "First Chapter")
    ]


def test_toc_empty_anchor_and_its_chapter_heading_are_one_boundary(tmp_path):
    path = _epub(
        tmp_path,
        [("one.xhtml", '<a id="chapter"/><h1>Chapter 1: First</h1><p>Body.</p>')],
        toc=_link("one.xhtml#chapter", "First"),
    )
    body = _render_body(extract(str(path)))
    assert _annotations(body, "chapter") == [1]
    assert _annotations(body, "chapter_title") == ["First"]


def test_frontmatter_toc_alias_uses_printed_label_without_duplicate_paragraph_points(
    tmp_path,
):
    # Observed shape: two root TOC links target the same span in the first
    # paragraph. Labels and prose here are synthetic, not copied from the book.
    path = _epub(
        tmp_path,
        [
            (
                "front.xhtml",
                (
                    '<p data-kindle-position="100"><span id="opening">Contributor Notes</span></p>'
                    '<p data-kindle-position="120">A fictional biography paragraph.</p>'
                ),
            )
        ],
        toc=_link("front.xhtml#opening", "Cover")
        + _link("front.xhtml#opening", "Contributor Notes"),
    )
    with pytest.warns(
        EpubExtractionWarning, match="using source-matched label 'Contributor Notes'"
    ):
        book = extract(str(path))
    body = _render_body(book)
    assert len(book.chapters) == 1
    assert _annotations(body, "chapter_title") == ["Contributor Notes"]
    assert not _annotations(body, "chapter")
    assert re.findall(r"\{\{_kindle_position: (\d+)\}\}", body) == ["100", "120"]
    assert re.search(r"\{\{_kindle_position: 100\}\}[ \t]*\n?Contributor Notes", body)
    assert body.count("A fictional biography paragraph.") == 1


def test_parent_and_child_toc_labels_at_one_target_select_the_printed_chapter(tmp_path):
    toc = (
        '<li><a href="one.xhtml#opening">Part One</a><ol>'
        + _link("one.xhtml#opening", "1. First")
        + "</ol></li>"
    )
    path = _epub(
        tmp_path,
        [("one.xhtml", '<h1 id="opening">Chapter 1: First</h1><p>Source prose.</p>')],
        toc=toc,
    )
    with pytest.warns(EpubExtractionWarning, match="TOC aliases"):
        body = _render_body(extract(str(path)))
    assert _annotations(body, "chapter") == [1]
    assert _annotations(body, "chapter_title") == ["First"]
    assert body.count("Source prose.") == 1


def test_unresolved_toc_aliases_do_not_invent_a_title_or_duplicate_chapters(tmp_path):
    path = _epub(
        tmp_path,
        [
            (
                "front.xhtml",
                '<p id="opening">Ordinary source prose, not a section label.</p>',
            )
        ],
        toc=_link("front.xhtml#opening", "Frontispiece")
        + _link("front.xhtml#opening", "Contents"),
    )
    with pytest.warns(EpubExtractionWarning, match="using source headings only"):
        body = _render_body(extract(str(path)))
    assert not _annotations(body, "chapter_title")
    assert not _annotations(body, "chapter")
    assert body.strip() == "Ordinary source prose, not a section label."


def test_duplicate_toc_label_at_same_target_is_one_boundary_without_warning(tmp_path):
    path = _epub(
        tmp_path,
        [("one.xhtml", '<h1 id="opening">1. First</h1><p>Source prose.</p>')],
        toc=_link("one.xhtml#opening", "1. First") * 2,
    )
    body = _render_body(extract(str(path)))
    assert _annotations(body, "chapter") == [1]
    assert _annotations(body, "chapter_title") == ["First"]


def test_parent_wrapper_and_leading_span_share_one_source_boundary(tmp_path):
    path = _epub(
        tmp_path,
        [
            (
                "one.xhtml",
                '<section id="section"><h1><span id="heading">Chapter 1: First</span></h1><p>Source prose.</p></section>',
            )
        ],
        toc=_link("one.xhtml#section", "Part One")
        + _link("one.xhtml#heading", "1. First"),
    )
    with pytest.warns(EpubExtractionWarning, match="TOC aliases"):
        body = _render_body(extract(str(path)))
    assert _annotations(body, "chapter") == [1]
    assert _annotations(body, "chapter_title") == ["First"]


def test_true_midparagraph_chapter_target_does_not_duplicate_the_paragraph_coordinate(
    tmp_path,
):
    path = _epub(
        tmp_path,
        [
            (
                "one.xhtml",
                '<p data-kindle-position="100">Earlier source text. <span id="next">Later source text.</span></p>',
            )
        ],
        toc=_link("one.xhtml#next", "2. Next"),
    )
    body = _render_body(extract(str(path)))
    assert body.count("{{_kindle_position: 100}}") == 1
    assert (
        body.index("Earlier source text.")
        < body.index("<!-- chapter: 2 -->")
        < body.index("Later source text.")
    )


def test_normalising_a_frontmatter_target_does_not_hide_a_later_explicit_chapter(
    tmp_path,
):
    path = _epub(
        tmp_path,
        [
            (
                "one.xhtml",
                '<p><span id="opening">Introduction</span></p><p>Introductory source prose.</p><h1>Chapter 1: First</h1><p>Chapter prose.</p>',
            )
        ],
        toc=_link("one.xhtml#opening", "Introduction"),
    )
    body = _render_body(extract(str(path)))
    assert _annotations(body, "chapter_title") == ["Introduction", "First"]
    assert _annotations(body, "chapter") == [1]
    assert body.index("Introductory source prose.") < body.index("<!-- chapter: 1 -->")


def test_source_number_not_accepted_as_chapter_metadata_is_not_deleted(tmp_path):
    path = _epub(
        tmp_path,
        [("one.xhtml", "<h1>Chapter 1</h1><p>Unnumbered section prose.</p>")],
        toc=_link("one.xhtml", "Notes"),
    )
    body = _render_body(extract(str(path)))
    assert not _annotations(body, "chapter")
    assert "# Chapter 1" in body


def test_chapter_title_annotation_round_trips_quotes_backslashes_and_delimiters(
    tmp_path,
):
    title = 'First: "quoted" \\ path --> end'
    path = _epub(
        tmp_path,
        [("one.xhtml", f"<h1>{escape(title)}</h1><p>Body.</p>")],
        toc=_link("one.xhtml", title),
    )
    assert _annotations(_render_body(extract(str(path))), "chapter_title") == [title]


@pytest.mark.parametrize("with_page", [False, True])
def test_heading_only_chapter_designation_coalesces_with_following_title(
    tmp_path, with_page
):
    page = '<span epub:type="pagebreak" title="015"/>' if with_page else ""
    path = _epub(
        tmp_path,
        [
            ("number.xhtml", f"<h1>{page}Chapter 1</h1>"),
            ("title.xhtml", "<h1>First Chapter</h1><p>Body.</p>"),
        ],
        toc=_link("number.xhtml", "Chapter 1") + _link("title.xhtml", "First Chapter"),
    )
    book = extract(str(path))
    assert len(book.chapters) == 1
    assert (book.chapters[0].number, book.chapters[0].title) == ("1", "First Chapter")
    body = _render_body(book)
    assert "# Chapter 1" not in body
    assert _annotations(body, "printed_page") == (["015"] if with_page else [])


def test_split_paragraph_chapter_retains_coincident_page_and_paragraph_points(tmp_path):
    path = _epub(
        tmp_path,
        [
            (
                "number.xhtml",
                '<p data-kindle-position="100"><span epub:type="pagebreak" title="15"/><strong>CHAPTER 1</strong></p>',
            ),
            (
                "title.xhtml",
                '<p data-kindle-position="110"><span epub:type="pagebreak" title="16"/><strong>First Chapter</strong></p><p>Body.</p>',
            ),
        ],
        toc=_link("number.xhtml", "Chapter 1") + _link("title.xhtml", "First Chapter"),
    )
    book = extract(str(path))
    assert len(book.chapters) == 1
    assert (book.chapters[0].number, book.chapters[0].title) == ("1", "First Chapter")
    body = _render_body(book)
    assert _annotations(body, "printed_page") == [15, 16]
    assert "{{_kindle_position: 100}}" not in body
    assert "{{_kindle_position: 110}}" in body


def test_later_subheading_is_not_borrowed_as_a_number_only_chapter_title(tmp_path):
    path = _epub(
        tmp_path,
        [
            (
                "one.xhtml",
                "<h1>Chapter 1</h1><p>Opening prose.</p><h2>A later subsection</h2><p>More.</p>",
            )
        ],
    )
    book = extract(str(path))
    assert [(c.number, c.title) for c in book.chapters] == [("1", None)]


@pytest.mark.parametrize("notes_first", [False, True])
def test_note_transfer_retains_unlinked_notes_and_unanchored_prose(
    tmp_path, notes_first
):
    refs = "".join(
        f'<a epub:type="noteref" href="Notes/notes.xhtml#n{i}">{i}</a>'
        for i in range(1, 5)
    )
    main = ("one.xhtml", f"<h1>1. First</h1><p>Claim{refs}</p>")
    notes = (
        "Notes/notes.xhtml",
        "<h1>Notes</h1><p>Unanchored introduction.</p>"
        + "".join(f'<p id="n{i}">{i}. Note body {i}.</p>' for i in range(1, 6)),
    )
    path = _epub(
        tmp_path,
        [notes, main] if notes_first else [main, notes],
        toc=_link("one.xhtml", "1. First") + _link("Notes/notes.xhtml", "Notes"),
    )
    body = _render_body(extract(str(path)))
    for i in range(1, 6):
        assert body.count(f"Note body {i}.") == 1
    assert "Unanchored introduction." in body
    assert "[^4]: Note body 4." in body


def test_note_internal_link_words_and_real_leading_year_survive(tmp_path):
    path = _epub(
        tmp_path,
        [
            (
                "one.xhtml",
                '<h1>1. First</h1><p>Claim<a epub:type="noteref" href="notes.xhtml#n1">1</a></p>',
            ),
            (
                "notes.xhtml",
                '<h1>Notes</h1><p id="n1">1947 was the year. See <a href="one.xhtml#section">Chapter Two</a> for evidence. <a role="doc-backlink" href="one.xhtml">Return</a></p>',
            ),
        ],
    )
    body = _render_body(extract(str(path)))
    assert "[^1]: 1947 was the year. See Chapter Two for evidence." in body
    assert "Return" not in body


def test_transferring_all_notes_does_not_delete_a_residual_source_heading(tmp_path):
    path = _epub(
        tmp_path,
        [
            (
                "one.xhtml",
                '<p>Claim<a epub:type="noteref" href="notes.xhtml#n1">1</a></p>',
            ),
            (
                "notes.xhtml",
                '<h1>Notes</h1><h2>Conflicting witness descriptions</h2><p id="n1">1. Source text.</p>',
            ),
        ],
    )
    body = _render_body(extract(str(path)))
    assert body.count("Source text.") == 1
    assert "## Conflicting witness descriptions" in body


def test_note_reference_in_discarded_navigation_cannot_spend_source_text(tmp_path):
    path = _epub(
        tmp_path,
        [
            (
                "one.xhtml",
                '<nav><a epub:type="noteref" href="notes.xhtml#n1">1</a></nav><p>Body.</p>',
            ),
            ("notes.xhtml", '<h1>Notes</h1><p id="n1">1. Source text.</p>'),
        ],
    )
    body = _render_body(extract(str(path)))
    assert "Source text." in body


def test_note_targets_do_not_collide_across_same_basename_documents(tmp_path):
    path = _epub(
        tmp_path,
        [
            (
                "one.xhtml",
                '<p>Claim<a epub:type="noteref" href="A/notes.xhtml#n1">1</a><a epub:type="noteref" href="B/notes.xhtml#n1">2</a></p>',
            ),
            ("A/notes.xhtml", '<h1>Notes</h1><p id="n1">1. First source.</p>'),
            ("B/notes.xhtml", '<h1>Notes</h1><p id="n1">1. Second source.</p>'),
        ],
    )
    body = _render_body(extract(str(path)))
    assert body.count("First source.") == body.count("Second source.") == 1
    assert "[^1]: First source." in body
    assert "[^2]: Second source." in body


def test_same_document_footnote_moves_only_the_evidenced_definition(tmp_path):
    path = _epub(
        tmp_path,
        [
            (
                "one.xhtml",
                '<h1>1. First</h1><p>Claim<a role="doc-noteref" href="#n1">1</a></p><aside role="doc-footnote" id="n1">1. Source text.</aside><p>Unrelated trailing prose.</p>',
            )
        ],
    )
    body = _render_body(extract(str(path)))
    assert body.count("Source text.") == 1
    assert "[^1]: Source text." in body
    assert "Unrelated trailing prose." in body


def test_rich_note_is_retained_in_place_with_its_image_and_page_marker(tmp_path):
    path = _epub(
        tmp_path,
        [
            (
                "one.xhtml",
                '<p>Claim<a epub:type="noteref" href="notes.xhtml#n1">1</a></p>',
            ),
            (
                "notes.xhtml",
                '<h1>Notes</h1><aside id="n1" epub:type="footnote"><p>Note text.</p><span epub:type="pagebreak" title="20"/><img src="one.png"/></aside>',
            ),
        ],
        media={"one.png": ("image/png", PNG)},
    )
    with pytest.warns(EpubExtractionWarning, match="Rich note kept"):
        body = _render_body(extract(str(path)))
    assert body.count("Note text.") == 1
    assert len(_annotations(body, "image")) == 1
    assert _annotations(body, "printed_page") == [20]


def test_cli_finalisation_keeps_media_and_source_identity_in_an_isolated_bundle(
    tmp_path,
):
    source = _epub(
        tmp_path,
        [
            (
                "one.xhtml",
                '<h1>Chapter 1: First</h1><p>Before.</p><figure><img src="one.png" alt="A picture"/><figcaption>Printed caption.</figcaption></figure><p>After.</p>',
            )
        ],
        media={"one.png": ("image/png", PNG)},
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "asset": source.name,
                "source": source.name,
                "fetched_at": "2026-09-27T00:00:00Z",
            }
        )
    )
    output = tmp_path / "isolated"
    assert run(tmp_path, output, force=False) == 0
    intermediate = next((output / "store").glob("*.md"))
    final = finalise_handler_record(intermediate, source, manifest, output).record_path
    document = read_record(final)
    result = validate(document.raw, expected_schema="anomalica/record/3")
    assert not result.errors
    assert (
        document.frontmatter["assets"][0]["asset_hash"]
        == "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
    )
    assert document.frontmatter["processing"]["asset_pipeline_versions"][0][
        "pipeline_version"
    ] == current_version("ebook")
    image = _annotations(document.body, "image")[0]
    assert image["caption"] == "Printed caption."
    assert (output / "media" / final.stem / image["file"]).read_bytes() == PNG
    assert all(alias.resolve() == final for alias in (output / "by-name").iterdir())
