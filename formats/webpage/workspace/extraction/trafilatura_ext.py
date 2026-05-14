"""Article extraction via trafilatura, with image augmentation from the source DOM."""

from __future__ import annotations

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

    augmented_text = augment_markdown(doc.text, harvest_images(html))

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
