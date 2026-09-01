"""Article extraction via trafilatura, with image augmentation from the source DOM."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree
from lxml import html as lxml_html
from trafilatura import bare_extraction

from extraction.images import MediaImage, harvest_images, render_images


@dataclass
class Article:
    text: str
    title: str | None
    authors: list[str] | None
    date: str | None
    sitename: str | None
    description: str | None
    media: list[MediaImage] = field(default_factory=list)


# Furniture containers that trafilatura otherwise pulls into the article body:
# newsletter/donation pitches, "latest stories" recirculation, social CTAs, and
# print-only chrome. They are stripped from the DOM before BOTH text extraction
# and image harvesting, so neither the body nor the appended image list carries
# page furniture. Deliberately conservative - each selector targets an
# unambiguous furniture class, never article prose. Extend per-site only after
# confirming a selector does not also match real content.
CHROME_XPATHS = [
    '//*[contains(@class, "newsletter")]',
    '//*[contains(@class, "latest-posts")]',
    '//*[contains(@class, "latest-stories")]',
    '//*[contains(@class, "third-party--")]',
    '//*[contains(@class, "print:hidden")]',
]


def strip_chrome(html: str) -> str:
    """Remove furniture containers (CHROME_XPATHS) from the DOM. Returns the
    original HTML unchanged if it cannot be parsed or nothing matched."""
    try:
        tree = lxml_html.fromstring(html)
    except (etree.ParserError, ValueError):
        return html
    removed = False
    for xpath in CHROME_XPATHS:
        for node in tree.xpath(xpath):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)
                removed = True
    if not removed:
        return html
    return lxml_html.tostring(tree, encoding="unicode")


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
    html = strip_chrome(html)
    doc = bare_extraction(
        html,
        url=url,
        with_metadata=True,
        # Emphasis OFF. Web pages over-style - bylines, photo credits, pull-quotes
        # and donate/footer boilerplate are all bold/italic - and trafilatura's
        # markdown emphasis on messy inline HTML comes out mangled (**** , spaces
        # inside ** **, stray unclosed **), which is pure manual-cleanup noise in a
        # faithful text record. Turning it off drops only the * markers; the text,
        # links, tables and images are all kept (verified: 94 asterisks -> 0, same
        # prose). A genuinely-needed emphasis is a reviewer's call, not the default.
        include_formatting=False,
        include_links=True,
        include_tables=True,
        include_images=True,
    )
    if doc is None or not doc.text or len(doc.text) < 10:
        return None

    body_text = _strip_leading_title(doc.text, doc.title)
    text, media = render_images(body_text, harvest_images(html))

    authors = None
    if doc.author:
        authors = [a.strip() for a in doc.author.split(";")]

    return Article(
        text=text,
        title=doc.title,
        authors=authors,
        date=doc.date,
        sitename=doc.sitename,
        description=doc.description,
        media=media,
    )
