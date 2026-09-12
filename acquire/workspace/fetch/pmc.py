"""Resolve PubMed Central PDF links through Europe PMC's stable PDF API."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import requests

from fetch.http import TIMEOUT, USER_AGENT

_PMC_PDF_PATH = re.compile(r"^/(?:pmc/)?articles/(PMC\d+)/pdf/[^/]+\.pdf$", re.I)
_PMC_HOSTS = {"pmc.ncbi.nlm.nih.gov", "www.ncbi.nlm.nih.gov"}


def resolved_pdf_url(url: str) -> str | None:
    """Europe PMC PDF endpoint for an NCBI PMC PDF URL, if recognised."""
    parsed = urlparse(url)
    if parsed.hostname not in _PMC_HOSTS:
        return None
    match = _PMC_PDF_PATH.match(parsed.path)
    if not match:
        return None
    return f"https://europepmc.org/api/getPdf?pmcid={match.group(1).upper()}"


def fetch(url: str) -> tuple[bytes, str, dict] | None:
    """Fetch a PMC PDF from Europe PMC, rejecting non-PDF responses."""
    resolved = resolved_pdf_url(url)
    if not resolved:
        return None
    try:
        response = requests.get(
            resolved, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
        )
        response.raise_for_status()
    except requests.RequestException:
        return None
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    if content_type != "application/pdf" or not response.content.lstrip().startswith(
        b"%PDF-"
    ):
        return None
    return response.content, content_type, {"fetched_url": resolved}
