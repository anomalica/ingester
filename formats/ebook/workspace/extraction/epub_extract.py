"""EPUB extraction via ebooklib - walks the spine and produces structured markdown."""

from __future__ import annotations

import hashlib
import posixpath
import re
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import unquote

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub
from markdownify import markdownify

# Markdownify escapes underscores in plain text to prevent emphasis collisions, so
# token markers must be pure alphanumerics. Round-tripping through markdownify
# preserves these markers verbatim.
IMG_TOKEN_PREFIX = "ANOMALICAIMG"
IMG_TOKEN_SUFFIX = "IMGEND"
IMG_TOKEN_RE = re.compile(rf"{IMG_TOKEN_PREFIX}([0-9a-f]{{12}}){IMG_TOKEN_SUFFIX}")

MIME_TO_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/tiff": "tiff",
}


@dataclass
class ExtractedImage:
    hash: str
    ext: str
    media_type: str
    bytes: bytes
    alt: str | None = None


@dataclass
class Chapter:
    index: int
    title: str | None
    markdown: str


@dataclass
class ExtractedBook:
    title: str
    authors: list[str] = field(default_factory=list)
    publisher: str | None = None
    language: str | None = None
    date_published: str | None = None
    description: str | None = None
    identifier: str | None = None
    chapters: list[Chapter] = field(default_factory=list)
    images: list[ExtractedImage] = field(default_factory=list)


def _meta_first(book: epub.EpubBook, namespace: str, name: str) -> str | None:
    items = book.get_metadata(namespace, name)
    if not items:
        return None
    value = items[0][0]
    return value.strip() if isinstance(value, str) and value.strip() else None


def _all_authors(book: epub.EpubBook) -> list[str]:
    items = book.get_metadata("DC", "creator")
    return [v.strip() for v, _ in items if isinstance(v, str) and v.strip()]


def _chapter_title(soup: BeautifulSoup) -> str | None:
    for tag_name in ("h1", "h2", "h3"):
        tag = soup.find(tag_name)
        if tag and tag.get_text(strip=True):
            return tag.get_text(" ", strip=True)
    return None


def _strip_navigation(soup: BeautifulSoup) -> None:
    for nav in soup.find_all(["nav", "script", "style"]):
        nav.decompose()


def _strip_internal_anchors(body) -> None:
    """Unwrap anchors pointing at EPUB-internal references.

    EPUB chapters cross-reference each other via `<a href="chapter2.xhtml">`
    or `<a href="#section">`. Once the book is flattened to a single markdown
    file, those hrefs resolve to nothing - every consumer (workbench,
    digester, assembler) would otherwise have to strip dead links.
    External links (http, https, mailto) are kept; internal ones are unwrapped,
    preserving their visible text.
    """
    for a in body.find_all("a"):
        href = (a.get("href") or "").strip()
        if href.startswith(("http://", "https://", "mailto:")):
            continue
        a.unwrap()


def _resolve_href(base_file: str, src: str) -> str:
    src = unquote(src.split("#", 1)[0].split("?", 1)[0])
    base_dir = posixpath.dirname(base_file)
    return posixpath.normpath(posixpath.join(base_dir, src)) if base_dir else src


def _ext_for(media_type: str, src: str) -> str:
    if media_type in MIME_TO_EXT:
        return MIME_TO_EXT[media_type]
    suffix = posixpath.splitext(src)[1].lstrip(".").lower()
    return suffix or "bin"


def _collect_images(
    body, chapter_file: str, book: epub.EpubBook, images: list[ExtractedImage]
) -> None:
    """Replace each <img> in the chapter body with a token, recording the image bytes.

    Tokens take the form __ANOMALICA_IMG_{12hex}__ and are expanded to image
    annotations after markdownify runs (markdownify mangles HTML comments
    inside the body, so we round-trip through a plain text token).
    """
    by_hash = {img.hash: img for img in images}
    for img_tag in body.find_all("img"):
        src = img_tag.get("src")
        if not src:
            img_tag.decompose()
            continue
        try:
            href = _resolve_href(chapter_file, src)
            item = book.get_item_with_href(href)
            if item is None:
                img_tag.decompose()
                continue
            img_bytes = item.get_content()
            img_hash = hashlib.sha256(img_bytes).hexdigest()[:12]
            alt = (img_tag.get("alt") or "").strip() or None

            existing = by_hash.get(img_hash)
            if existing is None:
                ext = _ext_for(item.media_type, src)
                new_img = ExtractedImage(
                    hash=img_hash,
                    ext=ext,
                    media_type=item.media_type,
                    bytes=img_bytes,
                    alt=alt,
                )
                images.append(new_img)
                by_hash[img_hash] = new_img
            elif existing.alt is None and alt:
                existing.alt = alt

            img_tag.replace_with(
                f"\n\n{IMG_TOKEN_PREFIX}{img_hash}{IMG_TOKEN_SUFFIX}\n\n"
            )
        except Exception:
            img_tag.decompose()


def _xhtml_to_markdown(
    xhtml: bytes,
    chapter_file: str,
    book: epub.EpubBook,
    images: list[ExtractedImage],
) -> tuple[str | None, str]:
    soup = BeautifulSoup(xhtml, "lxml-xml")
    _strip_navigation(soup)
    title = _chapter_title(soup)
    body = soup.find("body") or soup
    _strip_internal_anchors(body)
    _collect_images(body, chapter_file, book, images)
    md = markdownify(str(body), heading_style="ATX", strip=["script", "style"])
    md = "\n".join(line.rstrip() for line in md.splitlines())
    while "\n\n\n" in md:
        md = md.replace("\n\n\n", "\n\n")
    return title, md.strip()


def _yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _format_image_annotation(img: ExtractedImage) -> str:
    lines = ["<!--", "image:", f"  file: {img.hash}.{img.ext}"]
    if img.alt:
        lines.append(f"  alt: {_yaml_quote(img.alt)}")
    lines.append("-->")
    return "\n".join(lines)


def _expand_image_tokens(md: str, images: list[ExtractedImage]) -> str:
    by_hash = {img.hash: img for img in images}

    def replace(match: re.Match) -> str:
        img = by_hash.get(match.group(1))
        if img is None:
            return ""
        return _format_image_annotation(img)

    return IMG_TOKEN_RE.sub(replace, md)


def _spine_documents(book: epub.EpubBook) -> Iterable[epub.EpubItem]:
    seen: set[str] = set()
    for spine_id, _linear in book.spine:
        item = book.get_item_with_id(spine_id)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        if item.get_id() in seen:
            continue
        seen.add(item.get_id())
        yield item


def _patch_ebooklib_nav() -> None:
    """ebooklib raises IndexError on EPUB 3 nav files without `nav[epub:type='toc']`.
    The TOC nav is optional in EPUB 3; skip parsing rather than failing."""
    from ebooklib import epub as _epub

    original = _epub.EpubReader._parse_nav

    def safe_parse_nav(self, *args, **kwargs):
        try:
            return original(self, *args, **kwargs)
        except IndexError:
            return None

    _epub.EpubReader._parse_nav = safe_parse_nav


_patch_ebooklib_nav()


def extract(epub_path: str) -> ExtractedBook:
    """Parse an EPUB file and return structured chapters + metadata + images."""
    book = epub.read_epub(epub_path)

    title = _meta_first(book, "DC", "title") or "Untitled"
    publisher = _meta_first(book, "DC", "publisher")
    language = _meta_first(book, "DC", "language")
    date_published = _meta_first(book, "DC", "date")
    description = _meta_first(book, "DC", "description")
    identifier = _meta_first(book, "DC", "identifier")

    images: list[ExtractedImage] = []
    chapters: list[Chapter] = []
    for index, item in enumerate(_spine_documents(book), start=1):
        chapter_title, markdown = _xhtml_to_markdown(
            item.get_content(), item.file_name, book, images
        )
        markdown = _expand_image_tokens(markdown, images)
        if not markdown:
            continue
        chapters.append(Chapter(index=index, title=chapter_title, markdown=markdown))

    return ExtractedBook(
        title=title,
        authors=_all_authors(book),
        publisher=publisher,
        language=language,
        date_published=date_published,
        description=description,
        identifier=identifier,
        chapters=chapters,
        images=images,
    )
