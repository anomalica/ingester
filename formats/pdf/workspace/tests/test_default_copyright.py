from ingest_pdf import default_copyright, is_us_government_host


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
    """A local drop has unknown provenance, so it keeps the restricted default
    (None here -> _patch_frontmatter writes `restricted`)."""
    assert (
        default_copyright({"source": "/home/mark/some.pdf", "fetch_method": "local"})
        is None
    )
    assert default_copyright({}) is None


def test_explicit_status_wins():
    """An explicit --copyright (e.g. the war.gov importer's public_domain) overrides
    the URL default."""
    m = {"source": "https://www.war.gov/x.pdf", "copyright_status": "public_domain"}
    assert default_copyright(m) == {"status": "public_domain"}
