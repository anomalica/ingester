"""Copyright status defaults derived from how a source was acquired.

Every handler writes `copyright.status`, and until this module existed only the
PDF handler judged it - web and audio/video hardcoded `publicly_accessible`, so
a .gov press release came out gated behind proof-of-possession like a
copyrighted book, and `./ingest --copyright ...` was silently ignored on those
two paths.
"""

from __future__ import annotations

from urllib.parse import urlparse


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def is_us_government_host(url: str) -> bool:
    """True for a US federal host, whose works are public domain (17 USC 105).

    Matches the `.gov` and `.mil` TLDs (and their subdomains: apps.dtic.mil,
    www.congress.gov). NOT a hard rule - a government site can host a third-party
    contractor report that retains copyright - so this is a default a reviewer can
    override in the workbench, not a licence determination.
    """
    h = _host(url)
    return bool(h) and (h.endswith(".gov") or h.endswith(".mil") or h in ("gov", "mil"))


def default_status(manifest: dict) -> str | None:
    """The copyright status implied by a manifest, or None when it implies none.

    Three tiers, most specific first:

    - An operator's explicit `--copyright` wins outright.
    - A US GOVERNMENT source (.gov/.mil) is public domain by law - no copyright, so
      nothing to gate. It serves openly. Gating a DTIC report or a congress.gov
      hearing document behind proof-of-possession protects nothing and just hides
      the document from its reviewer.
    - Any other source FETCHED from a public URL is, by the fact that we retrieved
      it anonymously, publicly accessible. That still gates the original file (we
      don't redistribute someone else's copyrighted PDF) while letting the
      extracted text be surfaced.

    None means the manifest carries no URL at all - a local file of UNKNOWN
    provenance - and the caller applies its own conservative default. A local file
    whose origin the operator DECLARED with --source-url is not of unknown
    provenance, so it is judged on that URL. Without this the two routes to one
    document disagree: fetching a public PDF by URL yields `publicly_accessible`,
    while downloading that same PDF and ingesting it with its origin stamped
    yields `restricted` - and the second route is the one taken precisely when the
    URL is awkward (bot-blocked, dead, served from an archive), which has nothing
    to do with whether the document is public. Observed on Carlotto 2005: fetched
    from public sources, ingested as a file, gated behind proof-of-possession, and
    a reviewer sent to unlock a paper anyone can download.
    """
    if manifest.get("copyright_status"):
        return str(manifest["copyright_status"])
    # The fetched location first, then the declared origin.
    for candidate in (manifest.get("source"), manifest.get("source_url")):
        source = str(candidate or "")
        if source.startswith(("http://", "https://")):
            if is_us_government_host(source):
                return "public_domain"
            return "publicly_accessible"
    return None


def status_or(manifest: dict, fallback: str) -> str:
    """`default_status`, falling back to the handler's own conservative default."""
    return default_status(manifest) or fallback
