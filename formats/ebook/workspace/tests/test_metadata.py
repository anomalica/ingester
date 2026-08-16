"""Identifier scheme selection (item 7) and description gating (item 9)."""

from extraction.epub_extract import _pick_identifier, _scheme_and_value, _strip_html

from ingest_ebook import _gated_blurb


def test_pick_identifier_prefers_isbn_over_uuid_and_calibre():
    # Real shape: a book carrying uuid, calibre, ISBN and AMAZON in that order.
    items = [
        ("e2464dda-c2b1-495f-9ea8-e556a31aebc2", {"scheme": "uuid"}),
        ("e2464dda-c2b1-495f-9ea8-e556a31aebc2", {"scheme": "calibre"}),
        ("9780063235564", {"scheme": "ISBN"}),
        ("0063235560", {"scheme": "AMAZON"}),
    ]
    assert _pick_identifier(items) == "isbn:9780063235564"


def test_pick_identifier_bare_isbn13_gets_scheme():
    # The flagged corpus case: a lone bare ISBN-13, no scheme attribute.
    assert _pick_identifier([("9780190693503", {})]) == "isbn:9780190693503"


def test_pick_identifier_uuid_over_calibre_from_value_prefix():
    items = [
        ("calibre:4268", {}),
        ("uuid:faf61ae2-e75f-4f12-8347-aa42be57b2d8", {}),
    ]
    assert _pick_identifier(items) == "uuid:faf61ae2-e75f-4f12-8347-aa42be57b2d8"


def test_pick_identifier_urn_prefix_normalised():
    got = _pick_identifier([("urn:uuid:4bac550d-d7d5-48e7-8d66-c32a49d73a59", {})])
    assert got == "uuid:4bac550d-d7d5-48e7-8d66-c32a49d73a59"


def test_pick_identifier_bare_uuid_inferred():
    got = _pick_identifier([("4bac550d-d7d5-48e7-8d66-c32a49d73a59", {})])
    assert got == "uuid:4bac550d-d7d5-48e7-8d66-c32a49d73a59"


def test_pick_identifier_undetermined_stays_bare():
    # An unknown scheme is emitted bare rather than guessed onto a wrong one.
    assert _pick_identifier([("some-internal-ref-123", {})]) == "some-internal-ref-123"


def test_pick_identifier_none_when_empty():
    assert _pick_identifier([]) is None
    assert _pick_identifier([("   ", {})]) is None


def test_scheme_and_value_opf_namespaced_attr():
    # ebooklib may surface the OPF scheme under a namespaced attribute key.
    scheme, val = _scheme_and_value(
        "9781629143606", {"{http://www.idpf.org/2007/opf}scheme": "ISBN"}
    )
    assert (scheme, val) == ("isbn", "9781629143606")


def test_strip_html_reduces_markup_to_text():
    html = '<div style="font-size:150%"><p>A <strong>bold</strong> claim.</p></div>'
    assert _strip_html(html) == "A bold claim."


def test_strip_html_none_and_empty():
    assert _strip_html(None) is None
    assert _strip_html("<p>  </p>") is None


def test_gated_blurb_truncates_long_text():
    out = _gated_blurb("word " * 100)
    assert len(out) <= 204
    assert out.endswith("...")
    assert " wor..." not in out  # cut at a word boundary, not mid-word


def test_gated_blurb_keeps_short_text():
    assert _gated_blurb("A short blurb.") == "A short blurb."
