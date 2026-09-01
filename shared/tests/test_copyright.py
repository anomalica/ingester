from copyright import (
    MISSING_PROVENANCE_DETAIL,
    default_status,
    has_provenance,
    is_us_government_host,
    resolve,
    status_or,
)


def test_us_government_hosts_are_public_domain():
    """17 USC 105: a US government work carries no copyright, so there is nothing to
    gate. Gating one behind proof-of-possession hides the document from its reviewer
    for nothing."""
    for url in (
        "https://apps.dtic.mil/sti/tr/pdf/ADA568628.pdf",
        "https://www.congress.gov/117/meeting/house/114761/documents/X.pdf",
        "https://docs.house.gov/meetings/x.pdf",
        "https://www.defense.gov/News/Releases/Release/Article/2165713/",
        "https://burlison.house.gov/media/press-releases/x",
        "http://nasa.gov/x.pdf",
    ):
        assert default_status({"source": url}) == "public_domain", url


def test_match_is_on_the_hostname_not_a_substring():
    """A `.gov` in a path or query must not qualify - a substring match would open
    copyrighted material."""
    assert is_us_government_host("https://apps.dtic.mil/x.pdf")
    assert not is_us_government_host("https://example.com/fake.gov/report.pdf")
    assert not is_us_government_host("https://notgov.com/x?ref=congress.gov")
    assert not is_us_government_host("https://example.gov.uk/x")  # not a US .gov
    assert not is_us_government_host("not a url")


def test_other_public_urls_are_publicly_accessible():
    assert default_status({"source": "https://www.nytimes.com/2017/x"}) == (
        "publicly_accessible"
    )


def test_no_url_yields_no_judgement():
    """A local file of unknown provenance implies nothing; the caller applies its own
    conservative default rather than this module inventing one."""
    assert default_status({"source": "/home/mark/some.pdf"}) is None
    assert default_status({}) is None


def test_a_declared_origin_is_judged_like_a_fetched_one():
    """--source-url declares where a local file came from, and that is provenance."""
    assert (
        default_status(
            {
                "source": "/home/x/records/carlotto-2005.pdf",
                "source_url": "http://carlotto.us/papers/v04n04a.pdf",
            }
        )
        == "publicly_accessible"
    )
    assert (
        default_status(
            {"source": "/home/x/report.pdf", "source_url": "https://www.dtic.mil/x.pdf"}
        )
        == "public_domain"
    )


def test_explicit_status_wins():
    """`./ingest --copyright ...` is an operator's determination and beats the
    acquisition default. The web and audio handlers ignored it entirely before this
    module existed - they hardcoded publicly_accessible."""
    assert (
        default_status(
            {"source": "https://www.war.gov/x.pdf", "copyright_status": "licensed"}
        )
        == "licensed"
    )


def test_status_or_supplies_the_handler_default():
    """web and audio/video are always fetched, so their conservative default is
    publicly_accessible rather than the pdf handler's restricted."""
    assert status_or({}, "publicly_accessible") == "publicly_accessible"
    assert (
        status_or({"source": "https://www.defense.gov/a"}, "publicly_accessible")
        == "public_domain"
    )


# --- missing-provenance resolution (silent-gating fix) -----------------------


def test_resolve_missing_provenance_gets_the_detail():
    status, detail = resolve({}, fallback="restricted")
    assert status == "restricted"
    assert detail == MISSING_PROVENANCE_DETAIL


def test_resolve_public_url_has_no_detail():
    status, detail = resolve({"source": "https://example.com/x.pdf"})
    assert status == "publicly_accessible" and detail is None


def test_resolve_gov_url_public_domain():
    status, detail = resolve({"source_url": "https://www.congress.gov/x"})
    assert status == "public_domain" and detail is None


def test_resolve_declared_source_id_is_not_missing_provenance():
    # A declared origin means it's restricted for a real reason, not an unknown file.
    status, detail = resolve({"source_id": "youtube:ABC"}, fallback="restricted")
    assert status == "restricted" and detail is None


def test_has_provenance():
    assert has_provenance({"source_url": "https://x"})
    assert has_provenance({"source_id": "youtube:x"})
    assert has_provenance({"source": "http://x"})
    assert not has_provenance({"source": "/local/file.pdf"})
    assert not has_provenance({})
