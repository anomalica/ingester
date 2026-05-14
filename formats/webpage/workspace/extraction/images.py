"""Image augmentation - pulls alt text and captions from the source HTML
to enrich trafilatura's image emissions, and surfaces images that
trafilatura dropped entirely.

Trafilatura's image extraction is impoverished: it emits `![](url)` with
no alt text and no surrounding figcaption, and on some sites drops
images from the main article body altogether. This module reads the
same post-render HTML that trafilatura sees, finds every figure/img in
content-looking regions, and post-processes the markdown to splice in
the missing detail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from bs4 import BeautifulSoup

# Element tags that always indicate non-article chrome. We deliberately do
# NOT include <header> here: article-level <header> elements often contain
# the hero/featured image.
_NON_CONTENT_TAGS = ("nav", "aside", "footer", "script", "style", "noscript")

# Class-name words that mark non-article chrome. Matched as whole words
# against an ancestor's class list (split on whitespace, hyphens and
# underscores). Word-level matching prevents "right-sidebar" on a main
# content wrapper from filtering out everything inside it.
_NON_CONTENT_CLASS_WORDS = frozenset(
    {
        "nav",
        "navigation",
        "menu",
        "footer",
        "share",
        "sharing",
        "social",
        "promo",
        "advert",
        "subscribe",
        "newsletter",
        "comment",
        "modal",
        "cookie",
        "consent",
        "logo",
        "masthead",
        "breadcrumb",
        "related",
        "recommended",
        "popular",
    }
)

# Tokens that look like sidebar/header containers but should not be
# filtered when they appear alongside main-content tokens (e.g. a wrapper
# class "main ts-contain cf right-sidebar" describes a layout, not a
# sidebar). Filter on sidebar/header only when the class does NOT also
# claim to be article-level content.
_SIDEBAR_HINTS = frozenset({"sidebar", "side-bar"})
_HEADER_HINTS = frozenset({"header"})
_CONTENT_TOKENS = frozenset(
    {"main", "article", "post", "content", "entry", "story", "body"}
)

# URL fragments that almost always indicate non-article images.
_NON_CONTENT_URL_HINTS = (
    "/logo",
    "logo.",
    "/icons/",
    "icon-",
    "favicon",
    "share-",
    "/share/",
    "avatar",
)


_CLASS_SPLIT_RE = re.compile(r"[\s_/-]+")


def _class_tokens(ancestor) -> list[str]:
    """Return the lowercase whitespace/dash-separated tokens from an
    ancestor's class attribute, e.g. "main right-sidebar" -> ["main",
    "right", "sidebar"]."""
    if not hasattr(ancestor, "get"):
        return []
    cls_list = ancestor.get("class") or []
    if isinstance(cls_list, str):
        cls_list = [cls_list]
    joined = " ".join(cls_list).lower()
    return [t for t in _CLASS_SPLIT_RE.split(joined) if t]


# Inline image pattern in the trafilatura output. Matches `![alt](url)` with
# alt potentially empty. Used for both deduplication and alt/caption injection.
_IMG_LINE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

# Trafilatura/markdownify emits `**` to close a strong span and immediately
# opens another `**` across whitespace, leaving an empty bold pair that
# renders as four literal asterisks. Always safe to collapse to just the
# whitespace - an empty bold has no semantic content.
_EMPTY_BOLD_RE = re.compile(r"\*\*(\s+)\*\*")


@dataclass
class HarvestedImage:
    url: str
    alt: str | None
    caption: str | None


def _has_non_content_ancestor(node) -> bool:
    """True if the node lives inside a nav / footer / sidebar-style ancestor.

    Class-based filtering is skipped on <html> and <body> because those
    tags carry page-level layout markers (e.g. WordPress puts the active
    template name and "sidebar"/"right-sidebar" layout descriptors in the
    body class) that don't describe the descendant element."""
    for ancestor in node.parents:
        name = getattr(ancestor, "name", None)
        if name in _NON_CONTENT_TAGS:
            return True
        if name in ("html", "body", "[document]"):
            continue
        tokens = set(_class_tokens(ancestor))
        if tokens & _NON_CONTENT_CLASS_WORDS:
            return True
        # "sidebar"/"header" tokens are filtered only when the same class
        # doesn't also claim to be the main content wrapper. Classes like
        # "main ts-contain cf right-sidebar" describe a layout, not a
        # sidebar element, and should pass through.
        if (tokens & _SIDEBAR_HINTS) and not (tokens & _CONTENT_TOKENS):
            return True
        if (tokens & _HEADER_HINTS) and not (tokens & _CONTENT_TOKENS):
            return True
        role = (ancestor.get("role", "") if hasattr(ancestor, "get") else "").lower()
        if role in ("navigation", "banner", "complementary", "contentinfo"):
            return True
    return False


def _likely_chrome_url(url: str) -> bool:
    lowered = url.lower()
    return any(hint in lowered for hint in _NON_CONTENT_URL_HINTS)


def _resolve_img_url(img_tag) -> str | None:
    """Return the real image URL for an img tag, preferring non-placeholder
    attributes. Lazy-loading scripts often set src to a data-URI shim and
    put the real URL in data-src or data-srcset."""

    def _first_srcset_url(srcset: str) -> str | None:
        for part in srcset.split(","):
            url = part.strip().split()[0] if part.strip() else ""
            if url and not url.startswith("data:"):
                return url
        return None

    candidates = [
        img_tag.get("src"),
        img_tag.get("data-src"),
        img_tag.get("data-lazy-src"),
        img_tag.get("data-original"),
    ]
    for candidate in candidates:
        if candidate and not candidate.strip().startswith("data:"):
            return candidate.strip()

    for attr in ("srcset", "data-srcset", "data-lazy-srcset"):
        value = img_tag.get(attr)
        if value:
            found = _first_srcset_url(value)
            if found:
                return found

    return None


def _figcaption_text(img_tag) -> str | None:
    """Return the caption text associated with the img, if any.

    Recognises both HTML5 <figure>/<figcaption> and the WordPress
    [caption]-derived structure (div.wp-caption containing
    p.wp-caption-text)."""
    figure = img_tag.find_parent("figure")
    if figure is not None:
        cap = figure.find("figcaption")
        if cap is not None:
            text = cap.get_text(" ", strip=True)
            if text:
                return text

    wp_cap = img_tag.find_parent(class_="wp-caption")
    if wp_cap is not None:
        cap_text_el = wp_cap.find(class_="wp-caption-text")
        if cap_text_el is not None:
            text = cap_text_el.get_text(" ", strip=True)
            if text:
                return text

    return None


def harvest_images(html: str) -> list[HarvestedImage]:
    """Return content-region images with their alt text and figcaptions."""
    soup = BeautifulSoup(html, "lxml")
    found: list[HarvestedImage] = []
    seen_urls: set[str] = set()

    for img in soup.find_all("img"):
        url = _resolve_img_url(img)
        if not url or url in seen_urls:
            continue
        if _likely_chrome_url(url):
            continue
        if _has_non_content_ancestor(img):
            continue
        alt = (img.get("alt") or "").strip() or None
        caption = _figcaption_text(img)
        found.append(HarvestedImage(url=url, alt=alt, caption=caption))
        seen_urls.add(url)
    return found


def _index_by_url(images: Iterable[HarvestedImage]) -> dict[str, HarvestedImage]:
    return {img.url: img for img in images}


def augment_markdown(markdown: str, images: list[HarvestedImage]) -> str:
    """Splice alt text + captions into existing image references, dedupe
    duplicate URLs globally, and append content-region images that
    trafilatura dropped to the end of the document. Also cleans up empty
    bold pairs that trafilatura emits between adjacent strong tags.
    """
    markdown = _EMPTY_BOLD_RE.sub(r"\1", markdown)
    by_url = _index_by_url(images)
    emitted_urls: set[str] = set()
    output_lines: list[str] = []

    for line in markdown.splitlines():
        match = _IMG_LINE_RE.search(line)
        if match is None:
            output_lines.append(line)
            continue

        url = match.group(2).strip()
        existing_alt = match.group(1).strip()

        # Drop any repeat reference to a URL we've already emitted.
        # Trafilatura sometimes emits the same image twice (a wrapper +
        # its inner img, or srcset entries); we never want both.
        if url in emitted_urls:
            continue

        harvested = by_url.get(url)
        if harvested is None:
            output_lines.append(line)
            emitted_urls.add(url)
            continue

        alt = existing_alt or (harvested.alt or "")
        rebuilt = line.replace(match.group(0), f"![{alt}]({url})")
        output_lines.append(rebuilt)
        if harvested.caption:
            output_lines.append("")
            output_lines.append(f"*{harvested.caption}*")
        emitted_urls.add(url)

    # Anything trafilatura dropped is appended at the end so the reviewer
    # at least has access; exact position is lost but presence is
    # preserved. Skip images with no alt and no caption - those are most
    # likely chrome that snuck through.
    missing = [
        img
        for img in images
        if img.url not in emitted_urls and (img.caption or img.alt)
    ]
    if missing:
        output_lines.append("")
        output_lines.append("---")
        output_lines.append("")
        output_lines.append("**Additional images from this article:**")
        output_lines.append("")
        for img in missing:
            alt = img.alt or ""
            output_lines.append(f"![{alt}]({img.url})")
            if img.caption:
                output_lines.append("")
                output_lines.append(f"*{img.caption}*")
            output_lines.append("")

    return "\n".join(output_lines)
