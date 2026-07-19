from ingest_pdf import default_copyright


def test_url_fetched_pdf_is_publicly_accessible():
    """A PDF we retrieved from a public URL is publicly accessible by that fact -
    it must NOT default to `restricted`, which would gate its extracted text as if
    it were private. Matches the audio/video/web handlers."""
    m = {
        "source": "https://apps.dtic.mil/sti/tr/pdf/ADA568628.pdf",
        "fetch_method": "http",
    }
    assert default_copyright(m) == {"status": "publicly_accessible"}
    assert default_copyright({"source": "http://example.org/x.pdf"}) == {
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
