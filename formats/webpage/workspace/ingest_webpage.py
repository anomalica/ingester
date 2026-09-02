#!/usr/bin/env python3
"""Webpage ingester - extracts structured content from pre-fetched HTML."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from email_shape import (
    drop_leading_heading,
    extract_embedded_rfc822,
    parse_headers,
    render_email_frontmatter,
    render_thread_body,
    segment_thread,
    trim_raw_source_tail,
)
from copyright import status_or
from dates import normalise_published, published_scalar
from dedup import find_by_source_hash, find_by_source_id
from hashing import content_hash_label, hash_file, hash_string, store_exists
from pipeline_version import current_version
from publisher import canonical_publisher, strip_site_suffix
from record import get_version, write_record
from validator import validate
from verification import build_sidecar, needs_sidecar, write_sidecar

from extraction.trafilatura_ext import extract_article
from refresh import installed_version, refresh_record


_WAYBACK_RE = re.compile(r"web\.archive\.org/web/(\d{4})(\d{2})(\d{2})\d*/", re.I)


def _wayback_capture_date(fetched_url: str | None) -> date | None:
    """The capture date embedded in a Wayback Machine URL, or None.

    Wayback URLs are web.archive.org/web/<YYYYMMDDhhmmss>/<original>. That
    timestamp is when the page was ARCHIVED, never when it was published - so it
    must not become date_published, yet extractors happily scrape it from the
    archive's served-date chrome (a Space.com interview came out dated to its
    2001-04-13 capture rather than its 2001-02-27 byline).
    """
    if not fetched_url:
        return None
    m = _WAYBACK_RE.search(fetched_url)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _date_from_url(url: str | None, not_after: date) -> str | None:
    """Recover a publication date encoded in the URL path, as YYYY-MM-DD.

    Old news URLs often carry the date in the slug
    (.../clarke_believe_010227.html -> 2001-02-27). Only a candidate that parses
    to a real calendar date in [1990-01-01, not_after] is accepted - a page
    cannot be archived before it is published, so the capture date bounds it and
    rejects most article-id digit runs that merely look date-like. Used only to
    replace a date already known to be the Wayback capture, never as a general
    date source.
    """
    if not url:
        return None
    floor = date(1990, 1, 1)
    candidates: list[date] = []
    # 8-digit YYYYMMDD, optionally separated by / _ . or -.
    for m in re.finditer(
        r"(?<!\d)(19\d{2}|20\d{2})[/_.-]?(\d{2})[/_.-]?(\d{2})(?!\d)", url
    ):
        try:
            candidates.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    # 6-digit YYMMDD; century inferred and bounded by the capture date.
    for m in re.finditer(r"(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)", url):
        yy, mo, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        for century in (2000, 1900):
            try:
                cand = date(century + yy, mo, dd)
            except ValueError:
                continue
            if floor <= cand <= not_after:
                candidates.append(cand)
                break
    valid = [c for c in candidates if floor <= c <= not_after]
    if not valid:
        return None
    # The latest date on or before the capture is the best publication estimate.
    return max(valid).isoformat()


def _copyright_status(url: str) -> str:
    """Acquisition default per ingest-format.md: a `.gov`/`.mil` HOSTNAME is a US
    government work (17 USC 105) and carries no copyright -> `public_domain`; any
    other anonymously-fetched URL is provably `publicly_accessible`. Matches the
    hostname's final label, never a substring, so `example.com/fake.gov` and
    `example.gov.uk` do not qualify. This is the DEFAULT for a new acquisition -
    a reviewer can override it in the workbench.

    The judgement itself lives in shared/copyright.py so the pdf, web and
    audio/video handlers cannot drift apart on it again - they did, and a .gov
    press release came out gated behind proof-of-possession as a result.
    """
    return status_or({"source": url}, "publicly_accessible")


def _build_frontmatter(
    title: str,
    date_published: str,
    url: str,
    source_id: str | None,
    fetched_url: str | None,
    date_accessed: str | None,
    creators: list[str] | None,
    hex_hash: str,
    publisher: str | None,
    description: str | None,
    source_hash: str | None,
    snapshots: list[dict] | None,
    media_summary: dict | None,
    email_headers=None,
    copyright_status: str | None = None,
) -> str:
    """Assemble YAML frontmatter for a web record."""
    escaped_title = title.replace('"', '\\"')
    lines = [
        "---",
        "schema: anomalica/record/1",
        f'title: "{escaped_title}"',
        f"date_published: {published_scalar(date_published)}",
        "source_type: web",
        "file_format: html",
    ]
    # document_type is WHAT the record is; source_type is HOW it was acquired.
    # Set only when the whole record is one message - the email headers are the
    # artefact stating its form, which is derivation. A generic web page states no
    # form, so it is left absent rather than defaulted to `article`.
    if email_headers is not None:
        lines.append("document_type: email")
    lines.append(f"source_url: {url}")
    if publisher:
        escaped_pub = publisher.replace('"', '\\"')
        lines.append(f'publisher: "{escaped_pub}"')
    if source_id:
        lines.append(f"source_id: {source_id}")
    if fetched_url and fetched_url != url:
        lines.append(f"fetched_url: {fetched_url}")
    if creators:
        lines.append("creators:")
        for creator in creators:
            lines.append(f"  - {creator}")
    if description:
        escaped_desc = description.replace('"', '\\"')
        lines.append(f'description: "{escaped_desc}"')
    if email_headers is not None:
        # Carries only what the flat fields cannot - addresses, to/cc, subject and
        # the threading ids. date_published and creators come from the headers
        # above and are deliberately not repeated here.
        lines.extend(render_email_frontmatter(email_headers))
    lines.append(f"content_hash: {content_hash_label(hex_hash)}")
    if source_hash:
        lines.append(f"source_hash: {content_hash_label(source_hash)}")
    if snapshots:
        lines.append("snapshots:")
        for snap in snapshots:
            lines.append(f"  - role: {snap['role']}")
            lines.append(f"    hash: {content_hash_label(snap['hash'])}")
            lines.append(f"    content_type: {snap['content_type']}")
    if date_accessed:
        lines.append(f"date_accessed: {date_accessed}")
    lines.append(f"date_extracted: {datetime.now(timezone.utc).isoformat()}")
    lines.append("copyright:")
    lines.append(f"  status: {copyright_status or _copyright_status(url)}")
    if media_summary:
        lines.append("media:")
        lines.append(f"  count: {media_summary['count']}")
        lines.append(f"  total_bytes: {media_summary['total_bytes']}")
    lines.append("processing:")
    lines.append("  handler: webpage")
    lines.append(f"  version: {get_version()}")
    lines.append(f"  pipeline_version: {current_version('web')}")
    lines.append("  tools:")
    lines.append("    - name: trafilatura")
    lines.append(f'      version: "{installed_version("trafilatura")}"')
    lines.append("      role: extraction")
    lines.append("      provider: local")
    lines.append("---")
    return "\n".join(lines)


def run(staging_dir: Path, output_dir: Path, force: bool) -> int:
    """Run the webpage ingestion pipeline. Returns 0 on success, 1 on failure."""
    store_dir = output_dir / "store"
    by_name_dir = output_dir / "by-name"

    manifest_path = staging_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"Error: no manifest.json in {staging_dir}", file=sys.stderr)
        return 1

    manifest = json.loads(manifest_path.read_text())
    url = manifest["source"]
    asset_name = manifest["asset"]
    source_id = manifest.get("source_id")
    fetched_url = manifest.get("fetched_url")
    snapshots = manifest.get("snapshots") or []

    asset_path = staging_dir / asset_name
    if not asset_path.exists():
        print(f"Error: asset not found: {asset_path}", file=sys.stderr)
        return 1
    # The bytes the record is extracted from. acquire stamps the hash for a
    # fetched page; a local file (a re-process of the archived page) carries none,
    # and it is exactly then that the hash must be known - it is what says this
    # page is already in the store.
    source_hash = manifest.get("asset_hash") or hash_file(asset_path)

    html = asset_path.read_text(encoding="utf-8", errors="replace")

    # A page publishing a single email renders the readable message AND embeds
    # the raw RFC822 source; the raw block is the authoritative header source.
    # Detect it BEFORE extraction but do NOT remove it from the HTML - dropping
    # the <pre> changes what trafilatura scores as the main content and it picks
    # up the site's boilerplate instead. The raw tail is trimmed off the
    # extracted text below.
    raw_message = extract_embedded_rfc822(html)

    article = extract_article(html, url)
    if article is None:
        print("No article content extracted", file=sys.stderr)
        return 1

    print(f"Extracted: {article.title}", file=sys.stderr)

    # Email shape: a page publishing a single message embeds the raw RFC822
    # source alongside the rendered copy. Those headers are AUTHORITATIVE - the
    # page's own date furniture is not, which is how a 2015 email came out dated
    # 2000-01-01 (scraped off a date-picker config in the page's JS).
    email_headers = None
    if raw_message:
        email_headers = parse_headers(raw_message)
        readable = trim_raw_source_tail(article.text)
        # The page renders the subject as the body's leading heading; with it in
        # frontmatter that heading is furniture, not message content.
        readable = drop_leading_heading(readable, email_headers.subject)
        segments = segment_thread(readable, top_author=email_headers.from_)
        body_text = render_thread_body(
            segments,
            top_when=email_headers.date.isoformat() if email_headers.date else None,
        )
        print(
            f"Email: {len(segments)} message(s), "
            f"{len(email_headers.participants())} participant(s)",
            file=sys.stderr,
        )
    else:
        body_text = article.text

    # The same source bytes already have a live record: this is a re-extraction
    # of one page, not a second record of it. With --force the record is
    # refreshed IN PLACE under its existing identity (decision 0040); without,
    # there is nothing to do.
    existing = find_by_source_hash(store_dir, source_hash)
    if existing is not None:
        if not force:
            print(
                f"Skipping: these page bytes are already ingested as "
                f"{existing.stem[:12]}... (use --force to re-extract in place)",
                file=sys.stderr,
            )
            return 0
        outcome = refresh_record(
            existing,
            store_dir,
            body_text,
            asset_path,
            media_type="web",
            tool_version=installed_version("trafilatura"),
            extra_required=["source_url"],
        )
        print(f"Refresh {existing.stem[:12]}...: {outcome.reason}", file=sys.stderr)
        for note in outcome.notes:
            print(f"  {note}", file=sys.stderr)
        if outcome.reason.startswith("refused"):
            return 1
        if outcome.written and article.media:
            body = existing.read_text(encoding="utf-8")
            media_dir = output_dir / "media" / existing.stem
            media_dir.mkdir(parents=True, exist_ok=True)
            for img in article.media:
                name = f"{img.img_hash}.{img.ext}"
                if f"  file: {name}" in body:  # a stored file may have won
                    (media_dir / name).write_bytes(img.data)
        return 0

    hex_hash = hash_string(body_text)

    # Web articles are URL-based: use source_id from acquire as the store key.
    # Falls back to content hash only if source_id is missing for some reason.
    if not force and store_exists(store_dir, hex_hash):
        print(
            f"Skipping: record already exists (hash: {hex_hash[:12]}...)",
            file=sys.stderr,
        )
        return 0

    if not force and source_id:
        existing = find_by_source_id(store_dir, source_id)
        if existing:
            print(
                f"Skipping: source_id '{source_id}' already ingested as "
                f"{existing.stem[:12]}... (use --force to re-extract)",
                file=sys.stderr,
            )
            return 0

    if email_headers is not None and email_headers.date:
        date_published = email_headers.date.date().isoformat()
    else:
        extracted = article.date
        capture = _wayback_capture_date(fetched_url)
        if capture is not None and extracted == capture.isoformat():
            # The extractor took the Wayback served-date chrome for the
            # publication date. Recover the real date from the original URL slug
            # if it carries one; otherwise leave the capture date but flag it -
            # it is the archive date, not necessarily publication.
            recovered = _date_from_url(url, not_after=capture)
            if recovered:
                print(
                    f"Discarding Wayback capture date {extracted}; recovered "
                    f"publication date {recovered} from the original URL slug",
                    file=sys.stderr,
                )
                extracted = recovered
            else:
                print(
                    f"Warning: extracted date {extracted} equals the Wayback "
                    "capture date and no publication date is recoverable from the "
                    "URL - it may be the archive's served date, not publication",
                    file=sys.stderr,
                )
        date_published = normalise_published(extracted) or datetime.now(
            timezone.utc
        ).strftime("%Y-%m-%d")
    date_accessed = manifest.get("fetched_at")
    # The page's own site name is chrome, not part of either field: it rides on the
    # end of the title AND arrives as the publisher with a tagline attached.
    title = strip_site_suffix(article.title or "Untitled", article.sitename, url)
    publisher = canonical_publisher(article.sitename, url)
    creators = article.authors
    if email_headers is not None:
        if email_headers.subject:
            title = email_headers.subject
        if email_headers.from_:
            creators = [email_headers.from_.name or email_headers.from_.address]
    media_summary = None
    if article.media:
        media_summary = {
            "count": len(article.media),
            "total_bytes": sum(len(img.data) for img in article.media),
        }
    frontmatter = _build_frontmatter(
        title,
        date_published,
        url,
        source_id,
        fetched_url,
        date_accessed,
        creators,
        hex_hash,
        publisher,
        article.description,
        source_hash,
        snapshots,
        media_summary,
        email_headers,
        copyright_status=status_or(manifest, "publicly_accessible"),
    )
    content = frontmatter + "\n\n" + body_text + "\n"

    result = validate(content, extra_required=["source_url"])
    if result.fixed:
        content = result.fixed
    for warning in result.warnings:
        print(f"Validation warning: {warning}", file=sys.stderr)
    for error in result.errors:
        print(f"Validation error: {error}", file=sys.stderr)

    record_path, link_path = write_record(
        store_dir,
        by_name_dir,
        hex_hash,
        content,
        date_published,
        "web",
        title,
        force=force,
    )
    print(f"Written: {record_path}", file=sys.stderr)
    print(f"Symlink: {link_path}", file=sys.stderr)

    if article.media:
        media_dir = output_dir / "media" / hex_hash
        media_dir.mkdir(parents=True, exist_ok=True)
        for img in article.media:
            (media_dir / f"{img.img_hash}.{img.ext}").write_bytes(img.data)
        print(
            f"Media: {len(article.media)} images -> {media_dir}",
            file=sys.stderr,
        )

    if needs_sidecar(content):
        sidecar = build_sidecar(content, source_path=asset_path)
        sidecar_path = write_sidecar(store_dir, hex_hash, sidecar)
        print(
            f"Verification: {sidecar_path.name} ({len(sidecar.get('challenges', []))} challenges)",
            file=sys.stderr,
        )
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Extract content from pre-fetched HTML into Anomalica record format."
    )
    parser.add_argument("staging_dir", type=Path, help="Path to staging directory")
    parser.add_argument(
        "--force", action="store_true", help="Re-extract even if output exists"
    )
    args = parser.parse_args()

    output_dir = Path("/mnt/output")
    if not output_dir.exists():
        output_dir = (
            Path(__file__).resolve().parent.parent.parent.parent.parent / "ingests"
        )

    sys.exit(run(args.staging_dir, output_dir, args.force))


if __name__ == "__main__":
    main()
