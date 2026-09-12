from unittest.mock import Mock, patch

from fetch.pmc import fetch, resolved_pdf_url


PMC_URL = "https://pmc.ncbi.nlm.nih.gov/articles/PMC7514271/pdf/entropy-21-00939.pdf"
EUROPE_PMC_URL = "https://europepmc.org/api/getPdf?pmcid=PMC7514271"


def test_resolved_pdf_url_maps_pmcid_deterministically():
    assert resolved_pdf_url(PMC_URL) == EUROPE_PMC_URL
    assert resolved_pdf_url(f"{PMC_URL}?download=1") == EUROPE_PMC_URL
    assert (
        resolved_pdf_url("https://example.com/articles/PMC7514271/pdf/paper.pdf")
        is None
    )
    assert resolved_pdf_url("https://pmc.ncbi.nlm.nih.gov/articles/PMC7514271/") is None


@patch("fetch.pmc.requests.get")
def test_fetch_returns_verified_europe_pmc_pdf(mock_get):
    response = Mock()
    response.content = b"%PDF-1.7 fixture"
    response.headers = {"Content-Type": "application/pdf"}
    response.raise_for_status.return_value = None
    mock_get.return_value = response

    assert fetch(PMC_URL) == (
        b"%PDF-1.7 fixture",
        "application/pdf",
        {"fetched_url": EUROPE_PMC_URL},
    )
    mock_get.assert_called_once_with(
        EUROPE_PMC_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
        },
        timeout=30,
    )


@patch("fetch.pmc.requests.get")
def test_fetch_rejects_html_from_pdf_api(mock_get):
    response = Mock()
    response.content = b"<html>Preparing to download ...</html>"
    response.headers = {"Content-Type": "text/html"}
    response.raise_for_status.return_value = None
    mock_get.return_value = response

    assert fetch(PMC_URL) is None
