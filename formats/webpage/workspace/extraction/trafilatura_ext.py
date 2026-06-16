"""Article extraction via trafilatura, with image augmentation from the source DOM."""

from __future__ import annotations

import re
from dataclasses import dataclass

from trafilatura import bare_extraction

from extraction.images import augment_markdown, harvest_images


@dataclass
class Article:
    text: str
    title: str | None
    authors: list[str] | None
    date: str | None
    sitename: str | None
    description: str | None


def _normalise_heading(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


def _strip_leading_title(text: str, title: str | None) -> str:
    """Drop a leading markdown heading that merely repeats the article title.

    trafilatura emits the page title as the body's first heading; with the
    title now living in frontmatter only, that leading heading is a duplicate.
    Only strips when the heading text matches the title (normalised), so a
    genuine first heading that is not the title is left untouched.
    """
    if not title:
        return text
    stripped = text.lstrip("\n")
    m = re.match(r"#{1,6}[ \t]+(.+?)[ \t]*(?:\n|$)", stripped)
    if m and _normalise_heading(m.group(1)) == _normalise_heading(title):
        return stripped[m.end() :].lstrip("\n")
    return text


def extract_article(html: str, url: str | None = None) -> Article | None:
    """Extract article content and metadata from HTML.

    Args:
        html: The HTML string to extract from.
        url: Original URL (used by trafilatura for metadata context, not fetched).

    Returns:
        Article with text and metadata, or None if extraction fails.
    """
    doc = bare_extraction(
        html,
        url=url,
        with_metadata=True,
        include_formatting=True,
        include_links=True,
        include_tables=True,
        include_images=True,
    )
    if doc is None or not doc.text or len(doc.text) < 10:
        return None

    body_text = _strip_leading_title(doc.text, doc.title)
    augmented_text = augment_markdown(body_text, harvest_images(html))

    authors = None
    if doc.author:
        authors = [a.strip() for a in doc.author.split(";")]

    return Article(
        text=augmented_text,
        title=doc.title,
        authors=authors,
        date=doc.date,
        sitename=doc.sitename,
        description=doc.description,
    )
