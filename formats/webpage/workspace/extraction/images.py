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

import hashlib
import re
import sys
import time
from dataclasses import dataclass
from typing import Callable, Iterable

import requests
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
    "social-media",
    "social-icons",
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
    # The nearest runs of text BEFORE and AFTER the image in the page, so an
    # image the extractor dropped can be put back where it was. The text after
    # (its caption, the next paragraph) is the surer guide: page headers repeat
    # the author's name above a lead picture, and that would pull it down to
    # the byline. None before = nothing preceded it, the article's lead picture.
    anchor: str | None = None
    anchor_after: str | None = None
    width: int | None = None
    height: int | None = None


# An image whose declared size is below this on either side is an icon, an
# avatar or a tracking pixel, never a picture the article is showing.
_MIN_CONTENT_PX = 100
# Text runs shorter than this cannot anchor a position (a date, a label).
_MIN_ANCHOR_CHARS = 12


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


def _declared_size(img_tag) -> tuple[int | None, int | None]:
    """The image's own size claim: width/height attributes, or Squarespace's
    data-image-dimensions ("916x1191")."""

    def _px(value) -> int | None:
        m = re.match(r"\s*(\d+)", str(value or ""))
        return int(m.group(1)) if m else None

    width, height = _px(img_tag.get("width")), _px(img_tag.get("height"))
    dims = img_tag.get("data-image-dimensions") or ""
    m = re.match(r"(\d+)x(\d+)", dims)
    if m:
        width, height = width or int(m.group(1)), height or int(m.group(2))
    return width, height


def _is_tiny(width: int | None, height: int | None) -> bool:
    return any(v is not None and v < _MIN_CONTENT_PX for v in (width, height))


_BLOCK_TAGS = (
    "p",
    "figcaption",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "blockquote",
    "td",
    "dt",
    "dd",
    "pre",
    "div",
)


def _anchor_text(img_tag, after: bool = False) -> str | None:
    """The text of the nearest block before (or after) the image in document
    order that is long enough to be found again in the extracted body; None
    when there is none. Whole blocks, not text nodes: a caption written as
    "Photo by <a>Heidi Kaden</a> on <a>Unsplash</a>" is four short fragments
    and one twelve-word line. The page title is not in the body (it lives in
    frontmatter), so an h1 cannot place anything: a lead picture under the
    title has, for placement, nothing before it."""
    nodes = (
        img_tag.find_all_next(string=True)
        if after
        else img_tag.find_all_previous(string=True)
    )
    seen: set[int] = set()
    for node in nodes:
        parent = getattr(node, "parent", None)
        if parent is None or parent.name in ("script", "style", "noscript"):
            continue
        if not str(node).strip():
            continue  # whitespace between blocks would elect its wrapper div
        block = (
            parent if parent.name in _BLOCK_TAGS else parent.find_parent(_BLOCK_TAGS)
        )
        if block is None or id(block) in seen:
            continue
        seen.add(id(block))
        if block.name in ("h1", "title") or block.find("h1") is not None:
            continue
        if img_tag in block.descendants:
            continue  # the figure the image sits in is not text beside it
        text = " ".join(block.get_text(" ", strip=True).split())
        if len(text) >= _MIN_ANCHOR_CHARS:
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
        width, height = _declared_size(img)
        if _is_tiny(width, height):
            continue
        alt = (img.get("alt") or "").strip() or None
        caption = _figcaption_text(img)
        found.append(
            HarvestedImage(
                url=url,
                alt=alt,
                caption=caption,
                anchor=_anchor_text(img),
                anchor_after=_anchor_text(img, after=True),
                width=width,
                height=height,
            )
        )
        seen_urls.add(url)
    return found


def _index_by_url(images: Iterable[HarvestedImage]) -> dict[str, HarvestedImage]:
    return {img.url: img for img in images}


# A whole-line italic span, e.g. `*David Charles Grusch (Copyright (c) ...)*`.
# Trafilatura emits a source's printed caption as a loose italic paragraph on
# the line after the image; it is the caption, not article prose, and must move
# into the annotation so the pre-digest can strip it (it often carries a
# copyright notice that must never be read as a claim).
_ITALIC_LINE_RE = re.compile(r"^\s*[*_]([^*_].*?)[*_]\s*$")


def _norm(text: str) -> str:
    """Normalise caption text for comparison: collapse whitespace, drop a
    trailing full stop. Lets a plain prose caption line be matched against the
    figcaption it duplicates."""
    return re.sub(r"\s+", " ", text).strip().rstrip(".").strip()


_CONTENT_TYPE_EXT = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
}
_URL_EXT_RE = re.compile(r"\.(jpe?g|png|gif|webp|svg)(?:[?#]|$)", re.IGNORECASE)

# Fetcher contract: given an image URL, return (bytes, content_type) or None.
ImageFetch = Callable[[str], "tuple[bytes, str | None] | None"]


@dataclass
class MediaImage:
    """A downloaded image, keyed by a 12-char hash of its bytes."""

    img_hash: str
    ext: str
    data: bytes


def _yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _ext_for(content_type: str | None, url: str) -> str:
    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        if ct in _CONTENT_TYPE_EXT:
            return _CONTENT_TYPE_EXT[ct]
    match = _URL_EXT_RE.search(url)
    if match:
        ext = match.group(1).lower()
        return "jpg" if ext == "jpeg" else ext
    return "jpg"


def _default_fetch(url: str) -> tuple[bytes, str | None] | None:
    """Download image bytes, retrying transient failures (connection refused,
    timeout, 5xx) with backoff. A 404 is permanent - don't retry. Archive.org
    rate-limits bulk fetches, so a couple of backed-off retries turn a transient
    refusal into a success rather than a dropped image."""
    headers = {"User-Agent": "Mozilla/5.0 (anomalica-ingester)"}
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=30, headers=headers)
            if resp.status_code == 200 and resp.content:
                return resp.content, resp.headers.get("Content-Type")
            if resp.status_code == 404:
                return None
        except requests.RequestException:
            pass
        if attempt < 2:
            time.sleep(3 * (attempt + 1))
    return None


def _download(url: str, fetch: ImageFetch) -> MediaImage | None:
    """Fetch the image bytes; return a MediaImage or None on any failure
    (a rotted archive.org link must never block the ingest)."""
    try:
        result = fetch(url)
    except Exception as exc:  # network error, bad URL, timeout
        print(f"  image download failed ({url}): {exc}", file=sys.stderr)
        return None
    if not result or not result[0]:
        print(f"  image download failed ({url}): no content", file=sys.stderr)
        return None
    data, content_type = result
    img_hash = hashlib.sha256(data).hexdigest()[:12]
    return MediaImage(img_hash=img_hash, ext=_ext_for(content_type, url), data=data)


def _format_image_annotation(
    file: str | None, alt: str | None, caption: str | None
) -> str:
    lines = ["<!--", "image:"]
    if file:
        lines.append(f"  file: {file}")
    if alt:
        lines.append(f"  alt: {_yaml_quote(alt)}")
    if caption:
        lines.append(f"  caption: {_yaml_quote(caption)}")
    lines.append("-->")
    return "\n".join(lines)


def render_images(
    markdown: str,
    images: list[HarvestedImage],
    fetch: ImageFetch | None = None,
) -> tuple[str, list[MediaImage]]:
    """Replace trafilatura's ``![alt](url)`` image markdown with structured
    ``<!-- image: ... -->`` block annotations, downloading the bytes so the
    record no longer depends on remote (archive.org) URLs that rot.

    Captions come from the source figcaption (already on the HarvestedImage) or,
    failing that, the italic line immediately following the image - which is
    where a source's printed caption (often a copyright/attribution notice)
    lands. Either way the caption moves into the annotation, out of the body
    prose, so the pre-digest strips it before extraction.

    Returns the transformed markdown and the list of images to write to
    ``media/{record_hash}/``. On download failure the annotation is emitted
    without a ``file`` (alt/caption preserved) so a dead link never blocks
    ingestion.
    """
    if fetch is None:
        fetch = _default_fetch
    markdown = _EMPTY_BOLD_RE.sub(r"\1", markdown)
    by_url = _index_by_url(images)
    emitted_urls: set[str] = set()
    downloads: dict[str, MediaImage | None] = {}
    media: list[MediaImage] = []

    def resolve(url: str) -> MediaImage | None:
        if url not in downloads:
            mi = _download(url, fetch)
            downloads[url] = mi
            if mi is not None:
                media.append(mi)
        return downloads[url]

    lines = markdown.splitlines()
    output: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        match = _IMG_LINE_RE.search(line)
        if match is None:
            output.append(line)
            i += 1
            continue

        url = match.group(2).strip()
        existing_alt = match.group(1).strip()
        # Trafilatura sometimes emits the same image twice (wrapper + inner img,
        # or srcset entries); never emit both.
        if url in emitted_urls:
            i += 1
            continue

        harvested = by_url.get(url)
        alt = existing_alt or (harvested.alt if harvested else None) or None
        caption = harvested.caption if harvested else None

        # Fold the printed caption into the annotation. Look past blank lines to
        # the next content line and consume it when it is the caption: either a
        # whole-line italic (the caption when the image has no figcaption), or a
        # line (italic or plain) that duplicates the figcaption we already have
        # - trafilatura emits the figcaption as body prose too, which would
        # otherwise leave the caption in both places.
        next_i = i + 1
        while next_i < len(lines) and not lines[next_i].strip():
            next_i += 1
        consume_to = i
        if next_i < len(lines):
            cap_match = _ITALIC_LINE_RE.match(lines[next_i])
            candidate = (
                cap_match.group(1).strip() if cap_match else lines[next_i].strip()
            )
            if caption and _norm(candidate) == _norm(caption):
                consume_to = next_i  # duplicate of the figcaption - drop from prose
            elif cap_match and not caption:
                caption = candidate  # trailing italic line is the caption
                consume_to = next_i

        mi = resolve(url)
        file = f"{mi.img_hash}.{mi.ext}" if mi else None
        if file or alt or caption:
            output.append(_format_image_annotation(file, alt, caption))
            output.append("")
        emitted_urls.add(url)
        i = consume_to + 1

    # Content-region images trafilatura dropped entirely - above all an
    # article's lead picture, which sits before any text and has neither alt
    # nor caption. Each goes back after the paragraph it followed in the page;
    # one with no text before it leads the body. One whose preceding text is
    # not in the body at all followed something the extractor rejected - a
    # donate banner, a related-posts strip - and is rejected with it.
    for img in images:
        if img.url in emitted_urls:
            continue
        at = _anchor_position(output, img.anchor, img.anchor_after)
        if at is None:
            continue
        mi = resolve(img.url)
        file = f"{mi.img_hash}.{mi.ext}" if mi else None
        if not (file or img.alt or img.caption):
            continue
        # The caption trafilatura kept as loose prose right where the image goes
        # moves into the annotation, as it does for an image trafilatura emitted.
        if img.caption:
            nxt = at
            while nxt < len(output) and not output[nxt].strip():
                nxt += 1
            if nxt < len(output) and _norm(
                _ITALIC_LINE_RE.sub(r"\1", output[nxt]).strip()
            ) == _norm(img.caption):
                del output[at : nxt + 1]
                while at < len(output) and not output[at].strip():
                    del output[at]
        output[at:at] = [_format_image_annotation(file, img.alt, img.caption), ""]
        emitted_urls.add(img.url)

    text = re.sub(r"\n{3,}", "\n\n", "\n".join(output)).strip() + "\n"
    return text, media


def _squash(text: str) -> str:
    return re.sub(r"[\W_]+", "", re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)).lower()


# An anchor is a strong match for a body line when it covers at least half of
# the line's letters or is a long run in its own right; a short name inside a
# long byline is not evidence the image sat there.
_STRONG_ANCHOR_CHARS = 40
# A picture whose text-after sits this early in the body is a lead picture;
# what preceded it in the page was the header, which the body never carries.
_LEAD_WINDOW_LINES = 2


def _line_with(lines: list[str], text: str | None) -> int | None:
    """Index of the first body line the anchor text strongly matches."""
    key = _squash(text) if text else ""
    if not key:
        return None
    for i, line in enumerate(lines):
        if line.startswith("<!--"):
            continue
        line_key = _squash(line)
        if key in line_key and (
            len(key) >= _STRONG_ANCHOR_CHARS or 2 * len(key) >= len(line_key)
        ):
            return i
    return None


def _anchor_position(
    lines: list[str], before: str | None, after: str | None
) -> int | None:
    """Where in `lines` a dropped image goes: just before the line carrying the
    text that followed it in the page, when the text before it is also in the
    body (or there was none, or the image leads the body); failing that, just
    after the line carrying the text that preceded it; nowhere (None) when the
    body carries neither - the image followed something the extractor rejected."""
    nxt = _line_with(lines, after)
    prev = _line_with(lines, before)
    if nxt is not None:
        content_before = sum(
            1 for line in lines[:nxt] if line.strip() and not line.startswith("<!--")
        )
        if before is None or prev is not None or content_before <= _LEAD_WINDOW_LINES:
            return nxt
    if before is None:
        return 0
    if prev is None:
        return None
    return prev + 1 if prev + 1 >= len(lines) or lines[prev + 1].strip() else prev + 2
