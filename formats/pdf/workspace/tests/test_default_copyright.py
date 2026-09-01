from ingest_pdf import default_copyright
from shared.copyright import is_us_government_host


def test_us_government_sources_are_public_domain():
    """US government works are public domain (17 USC 105) - there is no copyright to
    protect, so gating one behind proof-of-possession hides the document from its
    reviewer for nothing. The two that prompted this: a DTIC report and a
    congress.gov hearing document, both of which arrived gated."""
    for url in (
        "https://apps.dtic.mil/sti/tr/pdf/ADA568628.pdf",
        "https://www.congress.gov/117/meeting/house/114761/documents/X.pdf",
        "https://www.war.gov/reading-room/doc.pdf",
        "http://nasa.gov/x.pdf",
    ):
        assert default_copyright({"source": url}) == {"status": "public_domain"}, url


def test_gov_match_is_on_the_host_not_the_path():
    """A `.gov` anywhere in the URL must not trigger it - only the actual hostname.
    A copyrighted site could carry '.gov' in a path or query."""
    assert is_us_government_host("https://apps.dtic.mil/x.pdf")
    assert is_us_government_host("https://congress.gov/x")
    assert not is_us_government_host("https://example.com/fake.gov/report.pdf")
    assert not is_us_government_host("https://notgov.com/x?ref=congress.gov")
    assert not is_us_government_host("https://example.gov.uk/x")  # not a US .gov
    assert not is_us_government_host("not a url")


def test_non_gov_url_stays_publicly_accessible():
    """A normal public URL is publicly accessible but NOT public domain - its
    original stays gated, only the extracted text is surfaced."""
    assert default_copyright({"source": "https://example.com/report.pdf"}) == {
        "status": "publicly_accessible"
    }


def test_url_fetched_pdf_is_publicly_accessible():
    """A PDF we retrieved from a public URL is publicly accessible by that fact -
    it must NOT default to `restricted`, which would gate its extracted text as if
    it were private. Matches the audio/video/web handlers. (A .gov/.mil source goes
    further still - see test_us_government_sources_are_public_domain.)"""
    m = {"source": "https://example.org/reports/x.pdf", "fetch_method": "http"}
    assert default_copyright(m) == {"status": "publicly_accessible"}
    assert default_copyright({"source": "http://example.com/x.pdf"}) == {
        "status": "publicly_accessible"
    }


def test_local_file_stays_conservative():
    """A local drop has unknown provenance, so it keeps the restricted default - but
    now EXPLICITLY, carrying the missing-provenance detail rather than a silent None,
    so the gating is visible and the record is findable."""
    from shared.copyright import MISSING_PROVENANCE_DETAIL

    block = default_copyright(
        {"source": "/home/mark/some.pdf", "fetch_method": "local"}
    )
    assert block["status"] == "restricted"
    assert block["detail"] == MISSING_PROVENANCE_DETAIL
    # an empty manifest is the same case - restricted, with the detail
    assert default_copyright({}) == {
        "status": "restricted",
        "detail": MISSING_PROVENANCE_DETAIL,
    }


def test_explicit_status_wins():
    """An explicit --copyright (e.g. the war.gov importer's public_domain) overrides
    the URL default."""
    m = {"source": "https://www.war.gov/x.pdf", "copyright_status": "public_domain"}
    assert default_copyright(m) == {"status": "public_domain"}


def test_a_declared_origin_url_is_judged_like_a_fetched_one():
    """--source-url declares where a local file came from, and that is provenance.

    Otherwise the two routes to one document disagree: fetching a public PDF by
    URL gives publicly_accessible, while downloading that same PDF and ingesting
    it with its origin stamped gives restricted - and the file route is taken
    precisely when the URL is awkward (bot-blocked, dead, archive-only), which
    says nothing about whether the document is public.

    Observed on Carlotto 2005: acquired from public sources, ingested as a file,
    then gated behind proof-of-possession.
    """
    assert default_copyright(
        {
            "source": "/home/x/records/carlotto-2005.pdf",
            "source_url": "http://carlotto.us/newfrontiersinscience/Papers/v04n04a/v04n04a.pdf",
        }
    ) == {"status": "publicly_accessible"}


def test_a_declared_government_origin_is_still_public_domain():
    assert default_copyright(
        {
            "source": "/home/x/records/report.pdf",
            "source_url": "https://www.dtic.mil/x.pdf",
        }
    ) == {"status": "public_domain"}


def test_a_local_file_with_no_declared_origin_stays_restricted():
    """No URL, no evidence: it stays gated (never silently servable), and now says so
    - restricted WITH the missing-provenance detail, not a bare None."""
    block = default_copyright({"source": "/home/x/records/unknown.pdf"})
    assert block["status"] == "restricted"
    assert "detail" in block


def test_an_explicit_status_still_wins():
    assert default_copyright(
        {
            "source": "/home/x/f.pdf",
            "source_url": "https://example.com/f.pdf",
            "copyright_status": "licensed",
        }
    ) == {"status": "licensed"}
