"""EPUB extraction via ebooklib - walks the spine and produces structured markdown."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub
from markdownify import markdownify


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


def _xhtml_to_markdown(xhtml: bytes) -> tuple[str | None, str]:
    soup = BeautifulSoup(xhtml, "lxml-xml")
    _strip_navigation(soup)
    title = _chapter_title(soup)
    body = soup.find("body") or soup
    md = markdownify(str(body), heading_style="ATX", strip=["script", "style"])
    md = "\n".join(line.rstrip() for line in md.splitlines())
    while "\n\n\n" in md:
        md = md.replace("\n\n\n", "\n\n")
    return title, md.strip()


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
    """Parse an EPUB file and return structured chapters + metadata."""
    book = epub.read_epub(epub_path)

    title = _meta_first(book, "DC", "title") or "Untitled"
    publisher = _meta_first(book, "DC", "publisher")
    language = _meta_first(book, "DC", "language")
    date_published = _meta_first(book, "DC", "date")
    description = _meta_first(book, "DC", "description")
    identifier = _meta_first(book, "DC", "identifier")

    chapters: list[Chapter] = []
    for index, item in enumerate(_spine_documents(book), start=1):
        chapter_title, markdown = _xhtml_to_markdown(item.get_content())
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
    )
