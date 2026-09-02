"""Article extraction via trafilatura, with image augmentation from the source DOM."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree
from lxml import html as lxml_html
from publisher import strip_site_suffix
from trafilatura import bare_extraction

from extraction.images import ImageFetch, MediaImage, harvest_images, render_images


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
    # Recirculation widgets ("Recommended Stories" and its thumbnails) dropped
    # into the article column, e.g. Marfeel's inline-recirc block on TIME.
    '//*[contains(@class, "recirc")]',
    '//*[contains(@class, "recommended-stories")]',
    "//*[@data-mrf-recirculation]",
]


# Inline emphasis is unwrapped from the DOM before extraction, so the record
# carries no bold/italic at all. Web pages over-style - bylines, photo credits,
# pull-quotes and donate/footer boilerplate are all bold or italic - and
# trafilatura's handling of inline emphasis is broken in two ways: the markdown
# comes out mangled (****, spaces inside ** **, stray unclosed **) and, worse,
# the text on either side of an emphasised span is split into separate
# paragraphs and re-ordered ("he said." landing before its own quotation).
# Removing the tags first leaves trafilatura plain runs of text, so the prose
# comes out in order and formatting can stay on for what the record does want:
# headings, paragraph breaks, lists, quotes, links and tables.
EMPHASIS_TAGS = (
    "strong",
    "b",
    "em",
    "i",
    "u",
    "mark",
    "small",
    "s",
    "strike",
    "del",
    "ins",
)


def unwrap_emphasis(html: str) -> str:
    """Remove inline emphasis tags (EMPHASIS_TAGS), keeping their text in place.
    Returns the original HTML unchanged if it cannot be parsed."""
    try:
        tree = lxml_html.fromstring(html)
    except (etree.ParserError, ValueError):
        return html
    etree.strip_tags(tree, *EMPHASIS_TAGS)
    return lxml_html.tostring(tree, encoding="unicode")


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


def _strip_leading_title(
    text: str, title: str | None, sitename: str | None = None, url: str | None = None
) -> str:
    """Drop a leading markdown heading that merely repeats the article title.

    trafilatura emits the page title as the body's first heading; with the
    title now living in frontmatter only, that leading heading is a duplicate.
    Only strips when the heading text matches the title (normalised) - with or
    without the site's trailing chrome ("... — Liberation Times | ...") - so a
    genuine first heading that is not the title is left untouched.
    """
    if not title:
        return text
    stripped = text.lstrip("\n")
    m = re.match(r"#{1,6}[ \t]+(.+?)[ \t]*(?:\n|$)", stripped)
    if not m:
        return text
    heading = _normalise_heading(m.group(1))
    candidates = {title, strip_site_suffix(title, sitename, url)}
    if heading in {_normalise_heading(c) for c in candidates}:
        return stripped[m.end() :].lstrip("\n")
    return text


def extract_article(
    html: str, url: str | None = None, fetch: ImageFetch | None = None
) -> Article | None:
    """Extract article content and metadata from HTML.

    Args:
        html: The HTML string to extract from.
        url: Original URL (used by trafilatura for metadata context, not fetched).
        fetch: Image downloader; defaults to a plain HTTP fetch. Pass one that
            returns None to keep extraction offline.

    Returns:
        Article with text and metadata, or None if extraction fails.
    """
    html = unwrap_emphasis(strip_chrome(html))
    doc = bare_extraction(
        html,
        url=url,
        with_metadata=True,
        # Formatting stays ON for headings, paragraph breaks, lists and quotes;
        # turning it off flattens the body to one line per paragraph with no
        # blank lines and no heading markers. Emphasis never reaches it - see
        # unwrap_emphasis.
        include_formatting=True,
        include_links=True,
        include_tables=True,
        include_images=True,
    )
    if doc is None or not doc.text or len(doc.text) < 10:
        return None

    body_text = _strip_leading_title(doc.text, doc.title, doc.sitename, url)
    text, media = render_images(body_text, harvest_images(html), fetch=fetch)

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
