"""EPUB extraction via ebooklib - walks the spine and produces structured markdown."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import warnings
from copy import copy
from dataclasses import dataclass, field
from typing import Iterable
from urllib.parse import unquote, urlsplit

import ebooklib
import yaml
from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from ebooklib import epub
from markdownify import markdownify

from text_repair import rejoin_dropcaps  # re-exported for callers/tests

# Markdownify escapes underscores in plain text to prevent emphasis collisions, so
# token markers must be pure alphanumerics. Round-tripping through markdownify
# preserves these markers verbatim.
IMG_TOKEN_PREFIX = "ANOMALICAIMG"
IMG_TOKEN_SUFFIX = "IMGEND"
IMG_TOKEN_RE = re.compile(rf"{IMG_TOKEN_PREFIX}(\d+){IMG_TOKEN_SUFFIX}")

REDACTION_TOKEN_PREFIX = "ANOMALICAREDACTED"
REDACTION_TOKEN_SUFFIX = "REDEND"
REDACTION_TOKEN_RE = re.compile(
    rf"{REDACTION_TOKEN_PREFIX}(\d+){REDACTION_TOKEN_SUFFIX}"
)

# Tokens contain an index, never source text: labels may contain punctuation,
# Unicode, YAML syntax or even our token delimiters.
PAGE_TOKEN_PREFIX = "ANOMALICAPAGE"
PAGE_TOKEN_SUFFIX = "PGEND"
PAGE_TOKEN_RE = re.compile(rf"{PAGE_TOKEN_PREFIX}(\d+){PAGE_TOKEN_SUFFIX}")
_PAGE_SCALAR = r'(?:"(?:\\.|[^"\\])*"|[^\n<>]+?)'
PRINTED_PAGE_RE = re.compile(rf"<!-- printed_page: ({_PAGE_SCALAR}) -->")

# Anomalica Prometheus EPUBs carry Kindle renderer locations as data attributes
# on paragraphs. As with images and pagebreaks, use an alphanumeric token to
# preserve each annotation through markdownify.
KINDLE_TOKEN_PREFIX = "ANOMALICAKINDLE"
KINDLE_TOKEN_SUFFIX = "KINDLEEND"
KINDLE_TOKEN_RE = re.compile(rf"{KINDLE_TOKEN_PREFIX}(\d+){KINDLE_TOKEN_SUFFIX}")

# Asterisk-based redaction patterns used in declassified-but-redacted material.
# Match either:
#   - five or more consecutive asterisks (a single redacted run), optionally
#     followed by space-separated continuation groups of one or more asterisks
#   - three or more consecutive asterisks WITH at least one space-separated
#     continuation group (multi-word redaction)
# Section-break "***" or "****" on its own does not match. Bold "**word**"
# never matches because the asterisks bracket non-asterisk text.
REDACTION_RE = re.compile(r"\*{5,}(?:\s+\*+)*|\*{3,}(?:\s+\*+)+")

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


@dataclass
class ImageOccurrence:
    image: ExtractedImage
    alt: str | None = None
    caption: str | None = None


class EpubExtractionError(ValueError):
    """The source cannot be represented without silently losing evidence."""


class EpubExtractionWarning(UserWarning):
    """Source evidence is retained, but a semantic association is unresolved."""


@dataclass(frozen=True)
class NavigationEntry:
    path: str
    fragment: str
    title: str
    logical: bool


@dataclass
class Chapter:
    index: int
    title: str | None
    markdown: str
    number: str | None = None


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


def _strip_html(text: str | None) -> str | None:
    """Plain text of a metadata value that may carry HTML markup.

    Publisher blurbs arrive in dc:description as HTML (`<p>`, `<strong>`, inline
    styles). A metadata field is not a body - the markup has no place in it and a
    consumer treating description as text would render the tags - so it is reduced
    to text with whitespace collapsed.
    """
    if not text:
        return None
    plain = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain or None


# Identifier schemes we recognise as a value-embedded prefix (isbn:X, urn:uuid:X).
_ID_PREFIX_SCHEMES = ("isbn", "uuid", "doi", "calibre", "asin", "amazon", "google")
# Preference order when a book carries several: ISBN is globally stable, the
# calibre id is a local library artefact. Lower rank wins.
_SCHEME_RANK = {"isbn": 0, "doi": 1, "uuid": 2, "calibre": 3}
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_ISBN13_RE = re.compile(r"^97[89]\d{10}$")


def _scheme_and_value(value: str, attrs: dict | None) -> tuple[str | None, str]:
    """Best (scheme, value) for one dc:identifier.

    A value-embedded prefix (`urn:isbn:`, `isbn:`, `uuid:`, `calibre:`) wins; then
    the OPF `scheme` attribute; then an unambiguous shape (ISBN-13, a UUID).
    Scheme is lowercased. None means undetermined - the value is emitted bare
    rather than guessed onto a wrong scheme.
    """
    v = value.strip()
    m = re.match(r"^(?:urn:)?([A-Za-z][\w-]*):(.+)$", v)
    if m and m.group(1).lower() in _ID_PREFIX_SCHEMES:
        return m.group(1).lower(), m.group(2).strip()
    scheme = None
    for key, val in (attrs or {}).items():
        if (
            key == "scheme"
            or key.endswith("}scheme")
            or key.lower().endswith(":scheme")
        ):
            scheme = str(val).strip().lower() or None
            break
    if scheme:
        return scheme, v
    digits = v.replace("-", "").replace(" ", "")
    if _ISBN13_RE.match(digits):
        return "isbn", digits
    if _UUID_RE.match(v):
        return "uuid", v
    return None, v


def _pick_identifier(items: Iterable) -> str | None:
    """The most useful identifier for a book, emitted as `scheme:value`.

    An EPUB carries several dc:identifier entries - ISBN, a publisher UUID, the
    calibre internal id - in no fixed order, and taking the first yielded a bare
    ISBN on one book and a bare UUID on the next, unschemed so `provenance.
    identifiers` had nowhere to key them. Prefer ISBN over DOI, UUID, calibre and
    emit the scheme.
    """
    best: tuple[int, str | None, str] | None = None
    for entry in items or []:
        value = entry[0] if isinstance(entry, (list, tuple)) else entry
        attrs = (
            entry[1] if isinstance(entry, (list, tuple)) and len(entry) > 1 else None
        )
        if not isinstance(value, str) or not value.strip():
            continue
        scheme, val = _scheme_and_value(value, attrs)
        rank = _SCHEME_RANK.get(scheme, 8 if scheme else 9)
        if best is None or rank < best[0]:
            best = (rank, scheme, val)
    if best is None:
        return None
    _, scheme, val = best
    return f"{scheme}:{val}" if scheme else val


# A block whose whole text is a bare Arabic chapter number ('3', '3.', 'Chapter
# 3'). Group 1 is the number. Used only for the loose "is this a number?" check.
_CHAPTER_NUMBER_RE = re.compile(r"^(?:chapter\s+)?(\d{1,4})\.?$", re.IGNORECASE)

_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")

# Chapter designations come in many forms across the corpus: Arabic ('1. The
# Secrecy'), Roman ('Chapter IV'), and spelled-out ('Chapter One', 'ONE'). All
# are normalised to a decimal string so a claim's location reads 'ch1:' whatever
# the book's own convention was.
_ONES = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_ROMAN_RE = re.compile(r"^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


def _word_to_int(text: str) -> int | None:
    """A spelled-out cardinal to its value: 'Twelve' -> 12, 'twenty-one' -> 21.
    Handles 1-99, which spans any real chapter count."""
    words = text.strip().lower().replace("-", " ").split()
    if len(words) == 1:
        return _ONES.get(words[0]) or _TENS.get(words[0])
    if len(words) == 2 and words[0] in _TENS and words[1] in _ONES:
        return _TENS[words[0]] + _ONES[words[1]]
    return None


def _roman_to_int(text: str) -> int | None:
    s = text.strip().upper()
    if not s or not _ROMAN_RE.match(s):
        return None
    total, prev = 0, 0
    for ch in reversed(s):
        value = _ROMAN_VALUES[ch]
        total += -value if value < prev else value
        prev = value
    return total


def _enum_to_int(token: str) -> int | None:
    """One enumerator token to an int, trying Arabic, then Roman, then a
    spelled-out cardinal. 'IV' -> 4, 'Twelve' -> 12, '7' -> 7; a word that is
    none of these ('Notes') -> None."""
    t = token.strip().rstrip(".")
    if t.isdigit():
        return int(t)
    roman = _roman_to_int(t)
    if roman is not None:
        return roman
    return _word_to_int(t)


def _is_chapter_number(text: str) -> bool:
    """A heading/line that is only an Arabic chapter number ('3', '3.',
    'Chapter 3')."""
    return bool(_CHAPTER_NUMBER_RE.match(text.strip()))


_DESIGNATION_TOKEN = r"([0-9]{1,4}|[A-Za-z]+(?:-[A-Za-z]+)?)"
_CHAPTER_PREFIX_RE = re.compile(
    rf"^chapter\s+{_DESIGNATION_TOKEN}\b[\s:.–—-]*(.*)$", re.IGNORECASE
)
_PART_PREFIX_RE = re.compile(
    rf"^part\s+{_DESIGNATION_TOKEN}\b[\s:.–—-]*(.*)$", re.IGNORECASE
)
_ARABIC_TITLE_RE = re.compile(r"^(\d{1,4})[.:)]\s+(.*\S)$")
_ROMAN_TITLE_RE = re.compile(r"^([IVXLCDM]+)\.\s+(.*\S)$")
_BARE_DESIGNATION_RE = re.compile(rf"^{_DESIGNATION_TOKEN}\.?$")

# A bare heading ('ONE', 'IV', '7') is only read as a chapter number below this
# ceiling. It rejects a stray page or endnote number ('178') and an all-caps
# word that happens to be a valid Roman numeral ('MIX' == 1009) from being
# mistaken for a chapter, while still covering any real chapter count.
_MAX_BARE_CHAPTER = 99


def _parse_designation(text: str | None) -> tuple[str | None, str | None, bool]:
    """Split a heading/TOC entry into (number, title, is_part).

    number is the chapter number as a decimal string, whatever notation the
    source used ('Chapter One' and 'Chapter I' and '1.' all give '1'), or None.
    title is the text with the designation removed. is_part marks a part divider
    ('Part One'), which carries a title but never a chapter number. An ambiguous
    Roman-numbered title ('II. Finding Our Liberty') stays verbatim.
    """
    t = (text or "").strip()
    if not t:
        return None, None, False
    m = _PART_PREFIX_RE.match(t)
    if m:
        return None, (m.group(2).strip() or None), True
    m = _CHAPTER_PREFIX_RE.match(t)
    if m and (n := _enum_to_int(m.group(1))) is not None:
        return str(n), (m.group(2).strip() or None), False
    m = _ARABIC_TITLE_RE.match(t)
    if m:
        return m.group(1), m.group(2).strip(), False
    m = _ROMAN_TITLE_RE.match(t)
    if m:
        # This can number a chapter, a part or a subsection. Without an explicit
        # designation, keep the title verbatim instead of inventing a part.
        return None, t, False
    m = _BARE_DESIGNATION_RE.match(t)
    if m and (n := _enum_to_int(m.group(1))) is not None and n <= _MAX_BARE_CHAPTER:
        return str(n), None, False
    return None, t, False


def _is_pure_designation(text: str) -> bool:
    """The whole heading is just a number ('Chapter One', 'ONE', '1') with no
    title of its own."""
    number, title, is_part = _parse_designation(text)
    return number is not None and title is None and not is_part


def _heading_text(tag: Tag) -> str:
    # Do not insert spaces between inline nodes: a page point or styled drop-cap
    # can sit inside a word. Only source whitespace is collapsed for metadata.
    return re.sub(r"\s+", " ", PAGE_TOKEN_RE.sub("", tag.get_text())).strip()


def _analyse_body(body) -> tuple[str | None, str | None, object | None]:
    """The chapter's title, its number, and the node to strip from the body.

    Finds the opening title heading and the chapter number, which may sit in
    that heading ('Chapter One: ...'),
    in a bare block right above it ('1' or 'ONE' styled as its own line), or be
    the heading itself when the chapter has no title of its own. The number's
    node is returned so the caller can drop it - otherwise it survives markdownify
    as an orphan '1'. A part divider ('PART ONE' above the title) yields no number.
    """
    blocks = [
        (tag, text)
        for tag in body.find_all(_HEADINGS + ("p",))
        if (text := _heading_text(tag))
    ]
    headings = [
        (i, tag, text) for i, (tag, text) in enumerate(blocks) if tag.name in _HEADINGS
    ]
    if not headings:
        return None, None, None
    # A later subheading does not name prose preceding it. Explicit chapter
    # headings later in a document are separate DOM sections, not this opening.
    if any(
        not _is_pure_designation(text) and not _parse_designation(text)[2]
        for _, text in blocks[: headings[0][0]]
    ):
        return None, None, None

    idx, first_tag, text = headings[0]
    first_number, _, first_part = _parse_designation(text)
    if _is_pure_designation(text) or first_part:
        following = blocks[idx + 1] if idx + 1 < len(blocks) else None
        if (
            following is not None
            and following[0].name in _HEADINGS
            and not _is_pure_designation(following[1])
            and not _parse_designation(following[1])[2]
        ):
            next_number, next_title, _ = _parse_designation(following[1])
            return (
                next_title or following[1],
                (None if first_part else first_number or next_number),
                (first_tag if first_number else None),
            )
        return (
            (text if first_part else None),
            first_number,
            (first_tag if first_number else None),
        )

    number, title, _ = _parse_designation(text)
    title = title or text

    # A bare number block immediately above the title ('1' or 'ONE' styled alone).
    strip_node = None
    if number is None and idx > 0:
        prev_tag, prev_text = blocks[idx - 1]
        if _is_pure_designation(prev_text):
            number = _parse_designation(prev_text)[0]
            strip_node = prev_tag
    return title, number, strip_node


def _target(base_file: str, href: str) -> tuple[str, str]:
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        raise EpubExtractionError(f"External EPUB resource in {base_file}: {href!r}")
    path = (
        posixpath.normpath(
            posixpath.join(posixpath.dirname(base_file), unquote(parsed.path))
        )
        if parsed.path
        else base_file
    )
    return path, unquote(parsed.fragment)


def _toc_entries(book: epub.EpubBook) -> list[NavigationEntry]:
    """Keep package paths, fragment targets and chapter/part ancestry.

    Nested subsection links remain ordinary headings. A root entry, a child of a
    part, or an explicitly named Chapter is a logical boundary; TOC depth alone
    is not a chapter number. ebooklib has already resolved TOC package paths.
    """
    result: list[NavigationEntry] = []

    def walk(entries, depth=0, parent_part=False, in_chapter=False) -> None:
        for entry in entries:
            node, children = entry if isinstance(entry, (tuple, list)) else (entry, ())
            href = getattr(node, "href", None)
            title = (getattr(node, "title", None) or "").strip()
            number, _, is_part = _parse_designation(title)
            explicit = bool(_CHAPTER_PREFIX_RE.match(title) and number)
            logical = (
                depth == 0
                or parent_part
                or explicit
                or (bool(number) and not in_chapter)
            )
            if href and title:
                path, fragment = _target("", href)
                result.append(NavigationEntry(path, fragment, title, logical))
            if children:
                walk(
                    children,
                    depth + 1,
                    is_part,
                    not is_part and (in_chapter or (logical and bool(href))),
                )

    walk(book.toc)
    return result


def _page_list_targets(book: epub.EpubBook) -> dict[tuple[str, str], str]:
    """EPUB 3 page-list and EPUB 2 NCX pageTarget labels at exact DOM targets."""
    targets: dict[tuple[str, str], str] = {}

    def add(base_file, href, label):
        if not href or not label:
            return
        key = _target(base_file, href)
        if key in targets and targets[key] != label:
            raise EpubExtractionError(
                f"Conflicting page-list labels for {key}: {targets[key]!r}, {label!r}"
            )
        targets[key] = label

    for item in book.get_items():
        if "nav" in getattr(item, "properties", ()) or isinstance(item, epub.EpubNav):
            soup = BeautifulSoup(item.get_content(), "lxml-xml")
            for nav in soup.find_all("nav"):
                if _attr_contains(nav, "type", "page-list") or _attr_contains(
                    nav, "role", "doc-pagelist"
                ):
                    for link in nav.find_all("a", href=True):
                        add(
                            item.file_name, link["href"], link.get_text(" ", strip=True)
                        )
        elif item.media_type == "application/x-dtbncx+xml":
            soup = BeautifulSoup(item.get_content(), "lxml-xml")
            for page in soup.find_all("pageTarget"):
                content, label = page.find("content"), page.find("navLabel")
                if content is not None and label is not None:
                    add(
                        item.file_name,
                        content.get("src"),
                        label.get_text(" ", strip=True),
                    )
    return targets


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


# Footnote markers survive markdownify as a plain-text token and expand to a
# `[^N]` reference afterwards, the same round-trip trick images and pagebreaks
# use (markdownify mangles anything that looks like markup).
FN_TOKEN_PREFIX = "ANOMALICAFN"
FN_TOKEN_SUFFIX = "FNEND"
FN_TOKEN_RE = re.compile(rf"{FN_TOKEN_PREFIX}(\d+){FN_TOKEN_SUFFIX}")


def _attr_contains(tag, local_name: str, needle: str) -> bool:
    """True if any of the tag's attributes whose local name is `local_name`
    (ignoring namespace) contains the whitespace-delimited token `needle` - handles `epub:type` however
    BeautifulSoup exposes it."""
    for key, value in tag.attrs.items():
        local = key.rsplit(":", 1)[-1].rsplit("}", 1)[-1]
        if local == local_name and needle in str(value).split():
            return True
    return False


def _is_noteref(a) -> bool:
    """A note reference: the little superscript that points at a footnote or
    endnote. Recognised by `epub:type="noteref"`, `role="doc-noteref"`, or the
    common plain form of a linked superscript pointing at an in-book anchor."""
    if _attr_contains(a, "role", "doc-backlink") or _attr_contains(
        a, "type", "backlink"
    ):
        return False
    if _attr_contains(a, "type", "noteref") or _attr_contains(a, "role", "doc-noteref"):
        return True
    href = (a.get("href") or "").strip()
    if "#" in href and not href.startswith(("http://", "https://", "mailto:")):
        return a.find("sup") is not None or a.find_parent("sup") is not None
    return False


def _note_content(element) -> str:
    """A footnote definition's text as a single markdown line, with its return
    arrow and leading marker number stripped. Parsed with the HTML parser so no
    XML declaration is prepended to the fragment."""
    frag = BeautifulSoup(str(element), "html.parser")
    for a in frag.find_all("a"):
        # Only an evidenced backlink is disposable. Ordinary internal citations
        # retain their words, exactly like internal links in the main text.
        if (
            _attr_contains(a, "role", "doc-backlink")
            or _attr_contains(a, "type", "backlink")
            or a.get_text(strip=True) in {"↩", "↵", "↑", "↥", "↩︎", "↩️"}
        ):
            a.decompose()
    _strip_internal_anchors(frag)
    text = markdownify(str(frag), heading_style="ATX")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^[-*]\s+", "", text)
    # Never strip an arbitrary leading year/quantity as if it were a note number.
    marker = re.fullmatch(
        r"(?:n|fn|note|footnote|endnote)[_-]?(\d+)", element.get("id", ""), re.I
    )
    if marker:
        text = re.sub(rf"^(?:fn\s*)?{marker[1]}(?:[.)](?:\s+|$)|\s+)", "", text)
    return text.strip()


class _FootnoteResolver:
    """Resolves note references to their definitions across the whole book.

    A note reference in one chapter points, by href, at a definition that often
    lives in a different spine document (a shared endnotes section). Numbers are
    assigned once, book-wide, so every `[^N]` is unique in the flattened record;
    the definition is placed with the chapter that cites it. Dedicated notes
    documents are recorded so the caller can drop them - their content has been
    pulled into the per-chapter definitions and would otherwise appear twice."""

    def __init__(self, book: epub.EpubBook, page_targets=()) -> None:
        self.book = book
        self.counter = 0
        self.note_documents: set[str] = set()
        self.pulled: dict[str, set[str]] = {}
        self._soups: dict[str, BeautifulSoup | None] = {}
        self._contents: dict[tuple[str, str], str] = {}
        self._page_targets = set(page_targets)
        self._spine_files = {item.file_name for item in _spine_documents(book)}
        # Plan before rendering anything: a notes document may precede its
        # references in the spine. Only transferred definitions may be removed.
        for item in _spine_documents(book):
            soup = self._soup(item.file_name)
            if soup is None:
                continue
            if self._is_notes_document(soup):
                self.note_documents.add(item.file_name)
            for a in (soup.find("body") or soup).find_all("a"):
                if (
                    _is_noteref(a)
                    and a.get("href")
                    and not a.find_parent(["nav", "script", "style"])
                ):
                    self._prepare(item.file_name, a["href"])

    @staticmethod
    def _is_notes_document(soup) -> bool:
        heading = soup.find(_HEADINGS)
        return bool(
            heading
            and heading.get_text(" ", strip=True).casefold()
            in {"notes", "endnotes", "footnotes"}
        ) or any(
            _attr_contains(tag, "type", kind)
            for tag in soup.find_all(["body", "section"])
            for kind in ("endnotes", "footnotes")
        )

    def _prepare(self, base_file: str, href: str) -> tuple[str, str]:
        target = _target(base_file, href)
        if target in self._contents:
            return target
        filename, fragment = target
        soup = self._soup(filename)
        element = soup.find(id=fragment) if soup is not None and fragment else None
        content = ""
        if element is not None:
            # Rich notes and source coordinates stay at their source positions.
            # Flattening them to a one-line footnote would lose images, structure
            # or print-page authority. The reference still gets a durable marker.
            nodes = [element, *element.find_all(True)]
            rich = any(
                tag.name
                in (
                    *_HEADINGS,
                    "img",
                    "svg",
                    "image",
                    "object",
                    "embed",
                    "canvas",
                    "table",
                    "pre",
                )
                or _is_pagebreak(tag)
                or tag.has_attr("data-kindle-position")
                or (filename, tag.get("id")) in self._page_targets
                or (tag.name == "a" and _is_noteref(tag))
                for tag in nodes
            )
            if not rich:
                content = _note_content(element)
            elif filename not in self._spine_files:
                raise EpubExtractionError(
                    f"Rich note outside the readable spine cannot be placed losslessly: {filename}#{fragment}"
                )
            else:
                warnings.warn(
                    f"Rich note kept in its source section: {filename}#{fragment}",
                    EpubExtractionWarning,
                )
            note_semantics = self._is_notes_document(soup) or any(
                _attr_contains(element, "type", kind)
                or _attr_contains(element, "role", f"doc-{kind}")
                for kind in ("footnote", "endnote")
            )
            if content and note_semantics:
                self.pulled.setdefault(filename, set()).add(fragment)
        else:
            warnings.warn(
                f"Unresolved note reference in {base_file}: {href!r}",
                EpubExtractionWarning,
            )
        self._contents[target] = content
        return target

    def _soup(self, filename: str) -> BeautifulSoup | None:
        if filename not in self._soups:
            item = self.book.get_item_with_href(filename)
            self._soups[filename] = (
                BeautifulSoup(item.get_content(), "lxml-xml") if item else None
            )
        return self._soups[filename]

    def resolve(self, base_file: str, href: str) -> tuple[str, str]:
        """Assign the next book-wide number to a reference and return
        (label, definition-text). The text is empty when the target cannot be
        found - the marker is still emitted rather than left as a bare digit."""
        self.counter += 1
        label = str(self.counter)
        target = self._prepare(base_file, href)
        return label, self._contents[target]

    def prune(self, body, filename: str) -> bool:
        """Remove transferred definitions, never a percentage of a document.

        Return true only for a spent notes document containing its generic Notes
        title and empty wrappers. Residual headings, introductions, notes, media
        and page markers all keep the document alive.
        """
        removed = False
        for fragment in self.pulled.get(filename, ()):
            element = body.find(id=fragment)
            if element is not None:
                element.decompose()
                removed = True
        if (
            not removed
            or filename not in self.note_documents
            or (filename, "") in self._page_targets
        ):
            return False
        heading = body.find(_HEADINGS)
        disposable_heading = (
            heading
            if heading
            and heading.get_text(" ", strip=True).casefold()
            in {"notes", "endnotes", "footnotes"}
            else None
        )
        return not any(
            str(text).strip()
            and (
                disposable_heading is None
                or text.find_parent(_HEADINGS) is not disposable_heading
            )
            for text in body.find_all(string=True)
        ) and not any(
            tag.name in {"img", "svg", "image", "hr"}
            or _is_pagebreak(tag)
            or (filename, tag.get("id")) in self._page_targets
            or tag.has_attr("data-kindle-position")
            for tag in body.find_all(True)
        )


def _collect_footnotes(
    body, chapter_file: str, resolver: _FootnoteResolver
) -> list[str]:
    """Replace each note reference in the body with a token and return the
    footnote definitions in reference order, as `[^N]: text` lines. Runs before
    internal anchors are unwrapped, so the reference links are still intact."""
    definitions = []
    for a in body.find_all("a"):
        if not _is_noteref(a):
            continue
        href = (a.get("href") or "").strip()
        if not href:
            continue
        label, content = resolver.resolve(chapter_file, href)
        a.replace_with(f"{FN_TOKEN_PREFIX}{label}{FN_TOKEN_SUFFIX}")
        if content:
            definitions.append(f"[^{label}]: {content}")
    return definitions


def _expand_footnote_tokens(md: str) -> str:
    return FN_TOKEN_RE.sub(lambda m: f"[^{m.group(1)}]", md)


def _resolve_href(base_file: str, src: str) -> str:
    return _target(base_file, src)[0]


def _ext_for(media_type: str, src: str) -> str:
    if media_type in MIME_TO_EXT:
        return MIME_TO_EXT[media_type]
    suffix = posixpath.splitext(src)[1].lstrip(".").lower()
    return suffix or "bin"


def _collect_images(
    body, chapter_file: str, book: epub.EpubBook, images: list[ExtractedImage]
) -> list[ImageOccurrence]:
    """Deduplicate bytes, not occurrences or their source-supplied metadata.

    Unresolved media fails extraction before the caller writes any record. A
    missing picture is not an empty picture, and alt text is not its replacement.
    """
    by_hash = {img.hash: img for img in images}
    occurrences: list[ImageOccurrence] = []
    unsupported = body.find(["object", "embed", "canvas"])
    if unsupported is not None:
        raise EpubExtractionError(
            f"Unsupported embedded {unsupported.name} in {chapter_file}; source retained in EPUB"
        )
    candidates = list(body.find_all(["img", "svg"]))
    figure_counts: dict[int, int] = {}
    for candidate in candidates:
        figure = candidate.find_parent("figure")
        if figure is not None:
            figure_counts[id(figure)] = figure_counts.get(id(figure), 0) + 1
    for container in candidates:
        img_tag = container
        if container.name == "svg":
            # A simple SVG image wrapper references original image bytes. A
            # composed vector drawing cannot be replaced by one of its members.
            members = container.find_all("image")
            if len(members) != 1 or any(
                tag.name not in {"image", "title", "desc"}
                for tag in container.find_all(True)
            ):
                raise EpubExtractionError(
                    f"Unsupported inline SVG drawing in {chapter_file}; source retained in EPUB"
                )
            img_tag = members[0]
        src = (
            img_tag.get("src")
            if img_tag.name == "img"
            else (img_tag.get("href") or img_tag.get("xlink:href"))
        )
        if not src:
            raise EpubExtractionError(
                f"Image without a resource reference in {chapter_file}: {str(container)!r}"
            )
        try:
            href = _resolve_href(chapter_file, src)
            item = book.get_item_with_href(href)
            if item is None:
                raise EpubExtractionError(
                    f"Unresolved image in {chapter_file}: {src!r} (package path {href!r})"
                )
            img_bytes = item.get_content()
            if not img_bytes:
                raise EpubExtractionError(
                    f"Empty image resource in {chapter_file}: {src!r}"
                )
            img_hash = hashlib.sha256(img_bytes).hexdigest()[:12]
            alt = (img_tag.get("alt") or "").strip() or None
            if container.name == "svg" and not alt:
                # SVG accessibility text is source metadata, not a generated
                # description or a printed caption.
                alt = (
                    img_tag.get("aria-label") or container.get("aria-label") or ""
                ).strip() or None
                if not alt:
                    alt = (
                        "\n".join(
                            _caption_text(tag)
                            for tag in container.find_all(["title", "desc"])
                        ).strip()
                        or None
                    )
            existing = by_hash.get(img_hash)
            if existing is None:
                ext = _ext_for(item.media_type, src)
                existing = ExtractedImage(
                    hash=img_hash,
                    ext=ext,
                    media_type=item.media_type,
                    bytes=img_bytes,
                )
                images.append(existing)
                by_hash[img_hash] = existing
            elif existing.bytes != img_bytes:
                raise EpubExtractionError(
                    f"Image hash-prefix collision in {chapter_file}: {src!r}"
                )
        except EpubExtractionError:
            raise
        except Exception as exc:
            raise EpubExtractionError(
                f"Cannot read image in {chapter_file}: {src!r}: {exc}"
            ) from exc

        caption_text = None
        figure = container.find_parent("figure")
        if figure is not None:
            captions = [
                tag
                for tag in figure.find_all("figcaption")
                if tag.find_parent("figure") is figure
            ]
            if len(captions) == 1:
                caption = captions[0]
                # Coordinates/references embedded in a caption cannot be moved
                # into a scalar without moving their source point. Keep this
                # ambiguous/rich caption as prose, with an explicit diagnostic.
                movable = not (
                    PAGE_TOKEN_RE.search(caption.get_text())
                    or FN_TOKEN_RE.search(caption.get_text())
                ) and not any(
                    tag.has_attr("data-kindle-position")
                    or (tag.name == "a" and _is_noteref(tag))
                    or tag.name in {"img", "svg", "table"}
                    for tag in [caption, *caption.find_all(True)]
                )
                if figure_counts[id(figure)] == 1 and movable:
                    caption_text = _caption_text(caption) or None
                    caption.decompose()
                else:
                    warnings.warn(
                        f"Caption kept as prose in {chapter_file}: figure has multiple images or a caption with source coordinates/references",
                        EpubExtractionWarning,
                    )
        index = len(occurrences)
        occurrences.append(ImageOccurrence(existing, alt, caption_text))
        container.replace_with(f"\n\n{IMG_TOKEN_PREFIX}{index}{IMG_TOKEN_SUFFIX}\n\n")
    return occurrences


def _caption_text(caption: Tag) -> str:
    """Visible caption text, preserving inline adjacency and explicit line breaks."""

    def walk(node):
        if isinstance(node, NavigableString):
            return re.sub(r"\s+", " ", str(node))
        if node.name == "br":
            return "\n"
        text = "".join(walk(child) for child in node.children)
        return f"\n{text}\n" if node.name in {"p", "div", "li"} else text

    text = walk(caption)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def _replace_redactions_in_soup(body) -> None:
    """Walk text nodes and replace asterisk-run redactions with placeholder tokens.

    Done before markdownify so that markdownify never sees the raw asterisks
    and never escapes them. The placeholder token is alphanumeric, so it
    passes through markdownify verbatim and is expanded to a `{{redacted}}`
    annotation in a later pass.
    """
    from bs4 import NavigableString

    for text_node in list(body.find_all(string=True)):
        if not isinstance(text_node, NavigableString):
            continue
        original = str(text_node)
        replaced = REDACTION_RE.sub(_redaction_to_token, original)
        if replaced != original:
            text_node.replace_with(replaced)


def _redaction_to_token(match: re.Match) -> str:
    run = match.group(0)
    word_count = len(re.findall(r"\*+", run))
    return f"{REDACTION_TOKEN_PREFIX}{word_count}{REDACTION_TOKEN_SUFFIX}"


def _expand_redaction_tokens(md: str) -> str:
    return REDACTION_TOKEN_RE.sub(
        lambda m: f"{{{{redacted: ~{m.group(1)} words}}}}",
        md,
    )


def _is_pagebreak(tag) -> bool:
    """True for an EPUB3 pagebreak marker - `epub:type="pagebreak"` or
    `role="doc-pagebreak"` - however BeautifulSoup exposes the (possibly
    namespaced) attribute name."""
    return _attr_contains(tag, "type", "pagebreak") or _attr_contains(
        tag, "role", "doc-pagebreak"
    )


def _pagebreak_label(tag) -> str | None:
    """An explicit label, or a narrowly evidenced page-number ID convention."""
    for attr in ("title", "aria-label"):
        label = tag.get(attr) or ""
        if label.strip():
            return label
    text = tag.get_text(strip=True)
    if text:
        return text
    tag_id = (tag.get("id") or "").strip()
    match = re.fullmatch(r"(?:page|pg|p)[_-]?([0-9]+|[ivxlcdm]+)", tag_id, re.I)
    if match and (match[1].isdigit() or _roman_to_int(match[1]) is not None):
        return match.group(1)
    return None


def _collect_pagebreaks(body, targets: dict[str, str] | None = None) -> list[str]:
    """Collect point markers before links/headings are stripped, adding no spaces.

    A page-list may point at a whole paragraph/section rather than an empty marker.
    Keep that content and put the coordinate at the target's start. Marker tags
    keep their IDs until logical chapter boundaries have also been resolved.
    """
    targets = dict(targets or {})
    labels: list[str] = []
    for tag in [body, *body.find_all(True)]:
        fragment = "" if tag is body and "" in targets else tag.get("id")
        listed = targets.pop(fragment, None)
        semantic = _is_pagebreak(tag)
        if not semantic and listed is None:
            continue
        explicit = _pagebreak_label(tag) if semantic else None
        # A page list can supply the label when the marker has only an opaque ID.
        if listed is not None and explicit is not None and listed != explicit:
            raise EpubExtractionError(
                f"Page-list/marker label conflict at {fragment!r}: {listed!r}, {explicit!r}"
            )
        label = listed if listed is not None else explicit
        if label is None:
            warnings.warn(
                f"Unlabelled EPUB pagebreak at {fragment!r}; no page number inferred",
                EpubExtractionWarning,
            )
            continue
        token = f"{PAGE_TOKEN_PREFIX}{len(labels)}{PAGE_TOKEN_SUFFIX}"
        labels.append(label)
        # Only the displayed label of a dedicated pagebreak is replaced. A listed
        # paragraph/heading, or a marker containing other prose, is never emptied.
        if semantic and (
            not tag.get_text(strip=True) or tag.get_text(strip=True) == label.strip()
        ):
            tag.clear()
        if tag.name in {"img", "image", "br", "hr", "input", "source"}:
            # Void elements have no rendered text children. Preserve the marker
            # immediately before them instead of losing it in HTML serialisation.
            tag.insert_before(token)
        else:
            tag.insert(0, token)
    if targets:
        raise EpubExtractionError(
            f"Missing EPUB page-list targets: {sorted(targets)!r}"
        )
    return labels


def _page_scalar(label: str) -> str:
    # Preserve established simple labels, but quote anything whose YAML type or
    # lexical value would change (015, true, null, punctuation, Unicode, etc.).
    if re.fullmatch(r"[0-9A-Za-z]+", label):
        parsed = yaml.safe_load(label)
        if type(parsed) in (str, int) and str(parsed) == label:
            return label
    return _yaml_quote(label)


def _expand_page_tokens(md: str, labels: list[str]) -> str:
    return PAGE_TOKEN_RE.sub(
        lambda m: f"<!-- printed_page: {_page_scalar(labels[int(m[1])])} -->", md
    )


def _collect_kindle_positions(body) -> list[str]:
    """Put a durable token at the start of each Kindle-positioned paragraph."""
    positions: list[str] = []
    for paragraph in body.find_all("p"):
        position = (paragraph.get("data-kindle-position") or "").strip()
        if not position.isdigit():
            continue
        index = len(positions)
        positions.append(position)
        paragraph.insert(0, f"{KINDLE_TOKEN_PREFIX}{index}{KINDLE_TOKEN_SUFFIX}")
    return positions


def _expand_kindle_tokens(md: str, positions: list[str]) -> str:
    def replace(match: re.Match) -> str:
        position = positions[int(match.group(1))]
        return f"{{{{_kindle_position: {position}}}}}"

    return KINDLE_TOKEN_RE.sub(replace, md)


def _disambiguate_page_sequences(chapters: list[Chapter]) -> None:
    """Add state markers when repeated page labels begin another sequence.

    Some updated ebooks splice newly paginated material into an older print
    sequence, then return to the old sequence for back matter. Sequence 1 is
    implicit; transitions are emitted only when duplicate labels would otherwise
    make page identity ambiguous.
    """
    sequences: list[dict] = [{"seen": set(), "last": None}]
    current = 0

    for chapter in chapters:

        def replace(match: re.Match) -> str:
            nonlocal current
            label = str(yaml.safe_load(match.group(1)))
            number = int(label) if re.fullmatch(r"[0-9]+", label) else None
            marker = match.group(0)

            if number is not None:
                continuation = next(
                    (
                        index
                        for index, state in enumerate(sequences)
                        if index != current and state["last"] == number - 1
                    ),
                    None,
                )
                if continuation is not None:
                    current = continuation
                    marker = f"<!-- printed_page_sequence: {current + 1} -->{marker}"
                else:
                    last = sequences[current]["last"]
                    collision = any(label in state["seen"] for state in sequences)
                    if last is not None and number < last and collision:
                        sequences.append({"seen": set(), "last": None})
                        current = len(sequences) - 1
                        marker = (
                            f"<!-- printed_page_sequence: {current + 1} -->{marker}"
                        )
                sequences[current]["last"] = number

            sequences[current]["seen"].add(label)
            return marker

        chapter.markdown = PRINTED_PAGE_RE.sub(replace, chapter.markdown)


# A pagebreak at the very start of a heading (the common per-chapter case)
# markdownifies inline: `## <!-- printed_page: 13 --> Chapter 2`.
_HEADING_PAGE_RE = re.compile(
    rf"^(#{{1,6}})[ \t]+((?:<!-- printed_page: {_PAGE_SCALAR} -->[ \t]*)+)(.*)$",
    re.MULTILINE,
)


def _hoist_heading_page_markers(md: str) -> str:
    """Lift page markers that landed inside a heading onto their own lines
    before it, keeping the own-line convention (`<!-- printed_page: 13 -->` then
    `## Chapter 2`). A heading that was only a pagebreak yields just the marker."""

    def repl(match: re.Match) -> str:
        markers = [m.group(0) for m in PRINTED_PAGE_RE.finditer(match.group(2))]
        title = match.group(3).strip()
        if title:
            markers.append(f"{match.group(1)} {title}")
        return "\n".join(markers)

    return _HEADING_PAGE_RE.sub(repl, md)


def _xhtml_to_markdown(
    xhtml: bytes,
    chapter_file: str,
    book: epub.EpubBook,
    images: list[ExtractedImage],
    resolver: _FootnoteResolver,
) -> tuple[str | None, str | None, str]:
    soup = BeautifulSoup(xhtml, "lxml-xml")
    _strip_navigation(soup)
    body = soup.find("body") or soup
    labels = _collect_pagebreaks(body)
    return _body_to_markdown(body, chapter_file, book, images, resolver, labels)


def _body_to_markdown(
    body, chapter_file, book, images, resolver, labels, *, strip_number=True
):
    title, number, number_tag = _analyse_body(body)
    if number_tag is not None and strip_number:
        # Keep a designation-only section until split-spine reconciliation. A
        # heading-only document is just as meaningful as a paragraph-only one.
        plain = PAGE_TOKEN_RE.sub("", body.get_text(" ", strip=True)).strip()
        designation = PAGE_TOKEN_RE.sub(
            "", number_tag.get_text(" ", strip=True)
        ).strip()
        if plain != designation or body.find(["img", "svg", "image"]):
            for marker in PAGE_TOKEN_RE.finditer(number_tag.get_text()):
                number_tag.insert_before(marker.group(0))
            number_tag.decompose()
    footnotes = _collect_footnotes(body, chapter_file, resolver)
    _strip_internal_anchors(body)
    occurrences = _collect_images(body, chapter_file, book, images)
    kindle_positions = _collect_kindle_positions(body)
    _replace_redactions_in_soup(body)
    # Whitespace has the same text meaning when emphasised. markdownify drops
    # whitespace-only emphasis, which otherwise joins "so<em> </em>quiet".
    # Work inside-out so nested formatting cannot hide the separator again.
    for formatting in reversed(body.find_all(["em", "strong", "i", "b"])):
        if formatting.get_text() and not formatting.get_text().strip():
            if formatting.find(True) is None:
                formatting.unwrap()
    md = markdownify(str(body), heading_style="ATX", strip=["script", "style"])
    md = rejoin_dropcaps(md)
    md = _expand_redaction_tokens(md)
    md = _expand_page_tokens(md, labels)
    md = _expand_footnote_tokens(md)
    md = _expand_kindle_tokens(md, kindle_positions)
    md = _hoist_heading_page_markers(md)
    md = _expand_image_tokens(md, occurrences)
    md = "\n".join(line.rstrip() for line in md.splitlines())
    while "\n\n\n" in md:
        md = md.replace("\n\n\n", "\n\n")
    md = md.strip()
    if footnotes:
        md = f"{md}\n\n" + "\n".join(footnotes)
    return title, number, md


def _yaml_quote(value: str) -> str:
    # JSON string syntax is valid YAML. Escape HTML delimiters as well so source
    # text containing '-->' cannot terminate the enclosing annotation.
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("<", r"\u003c")
        .replace(">", r"\u003e")
        .replace("\u0085", r"\u0085")
        .replace("\u2028", r"\u2028")
        .replace("\u2029", r"\u2029")
    )


def _format_image_annotation(occurrence: ImageOccurrence) -> str:
    img = occurrence.image
    lines = ["<!--", "image:", f"  file: {img.hash}.{img.ext}"]
    if occurrence.alt:
        lines.append(f"  alt: {_yaml_quote(occurrence.alt)}")
    if occurrence.caption:
        lines.append(f"  caption: {_yaml_quote(occurrence.caption)}")
    lines.append("-->")
    return "\n".join(lines)


def _expand_image_tokens(md: str, occurrences: list[ImageOccurrence]) -> str:
    return IMG_TOKEN_RE.sub(
        lambda m: _format_image_annotation(occurrences[int(m[1])]), md
    )


_KINDLE_POSITION_PREFIX_RE = re.compile(
    r"^\{\{_kindle_position[ \t]*:[ \t]*\d+[ \t]*\}\}"
)
_KINDLE_POSITION_LINE_RE = re.compile(
    r"^\{\{_kindle_position[ \t]*:[ \t]*\d+[ \t]*\}\}$"
)
_CHAPTER_BOUNDARY_MARKER_RE = re.compile(
    rf"^(?:<!-- printed_page(?:_sequence)?: {_PAGE_SCALAR} -->)+$"
)
_PAGE_POINT_RE = re.compile(rf"<!-- printed_page(?:_sequence)?: {_PAGE_SCALAR} -->")


def _plain_styled_line(line: str) -> str:
    """Remove only whole-line Markdown styling emitted for a source heading."""
    text = line.strip()
    text = re.sub(r"^#{1,6}[ \t]+", "", text)
    for marker in ("***", "___", "**", "__", "*", "_"):
        if (
            text.startswith(marker)
            and text.endswith(marker)
            and len(text) > 2 * len(marker)
        ):
            text = text[len(marker) : -len(marker)].strip()
            break
    return text


def _redundant_designation_markers(chapter: Chapter) -> list[str] | None:
    """Return independent boundary markers when this section is only `CHAPTER N`.

    The deliberately narrow check is what makes split-spine coalescing safe. The
    section must contain one explicitly labelled chapter designation and nothing
    else except EPUB page-boundary markers and a Kindle position attached to the
    designation paragraph. Page markers remain meaningful at the chapter boundary
    and are retained. The paragraph position is discarded with its redundant
    paragraph; moving it onto the following title would give that title two source
    positions, one of them false.
    """
    if chapter.number is None or chapter.title is not None:
        return None

    markers: list[str] = []
    content_lines: list[str] = []
    for line in chapter.markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        markers.extend(match.group(0) for match in _PAGE_POINT_RE.finditer(stripped))
        stripped = _PAGE_POINT_RE.sub("", stripped)
        stripped = _KINDLE_POSITION_PREFIX_RE.sub("", stripped)
        if stripped.strip():
            content_lines.append(stripped.strip())
    if len(content_lines) != 1:
        return None

    designation = _KINDLE_POSITION_PREFIX_RE.sub("", content_lines[0], count=1)
    designation = _plain_styled_line(designation)
    match = _CHAPTER_PREFIX_RE.fullmatch(designation)
    if match is None or match.group(2).strip():
        return None
    number = _enum_to_int(match.group(1))
    if number is None or str(number) != chapter.number:
        return None
    return markers


def _opens_with_title(chapter: Chapter) -> bool:
    """Whether a title-only section repeats its TOC title as its opening prose."""
    if chapter.number is not None or not chapter.title:
        return False
    for line in chapter.markdown.splitlines():
        text = line.strip()
        if not text or _CHAPTER_BOUNDARY_MARKER_RE.fullmatch(text):
            continue
        if _KINDLE_POSITION_LINE_RE.fullmatch(text):
            continue
        text = _PAGE_POINT_RE.sub("", text)
        text = _KINDLE_POSITION_PREFIX_RE.sub("", text, count=1)
        return _plain_styled_line(text) == chapter.title
    return False


def _coalesce_split_chapters(chapters: list[Chapter]) -> list[Chapter]:
    """Join a strict complementary pair split across adjacent spine documents.

    Some EPUBs put `CHAPTER 1` in one document and its title plus prose in the
    next. They are one logical chapter only when the first document is entirely
    redundant designation prose and the immediately adjacent, unnumbered document
    opens with the title that its TOC entry supplies.
    """
    coalesced: list[Chapter] = []
    index = 0
    while index < len(chapters):
        number_section = chapters[index]
        title_section = chapters[index + 1] if index + 1 < len(chapters) else None
        retained_markers = _redundant_designation_markers(number_section)
        if (
            title_section is not None
            and title_section.index == number_section.index + 1
            and retained_markers is not None
            and _opens_with_title(title_section)
        ):
            markdown_parts = [*retained_markers, title_section.markdown]
            coalesced.append(
                Chapter(
                    index=number_section.index,
                    title=title_section.title,
                    markdown="\n\n".join(part for part in markdown_parts if part),
                    number=number_section.number,
                )
            )
            index += 2
            continue
        coalesced.append(number_section)
        index += 1
    # An empty TOC anchor immediately followed by its own Chapter N heading is
    # one boundary. Do not emit a second chapter merely because both supply it.
    result: list[Chapter] = []
    for chapter in coalesced:
        previous = result[-1] if result else None
        same_identity = previous is not None and (
            (
                previous.title
                and previous.title == chapter.title
                and (
                    not previous.number
                    or not chapter.number
                    or previous.number == chapter.number
                )
            )
            or (
                previous.number
                and previous.number == chapter.number
                and (not previous.title or not chapter.title)
            )
        )
        if (
            same_identity
            and chapter.index == previous.index + 1
            and all(
                not line.strip() or _CHAPTER_BOUNDARY_MARKER_RE.fullmatch(line.strip())
                for line in previous.markdown.splitlines()
            )
        ):
            result[-1] = Chapter(
                chapter.index,
                previous.title or chapter.title,
                "\n\n".join(
                    part for part in (previous.markdown, chapter.markdown) if part
                ),
                previous.number or chapter.number,
            )
        else:
            result.append(chapter)
    return result


def _structural_kind(tag: Tag) -> str | None:
    for kind in ("chapter", "part"):
        if _attr_contains(tag, "type", kind) or _attr_contains(
            tag, "role", f"doc-{kind}"
        ):
            return kind
    return None


def _boundary_start(node: Tag, body: Tag) -> Tag:
    """Use one DOM boundary for wrappers beginning at the same source point.

    A TOC often names a span inside the opening paragraph. Splitting before that
    span would leave an empty paragraph in the previous section and duplicate its
    source position on the cloned wrapper. Move only across comments/whitespace,
    never across source text, another element or a page token.
    """
    while node is not body and isinstance(node.parent, Tag):
        if any(
            not isinstance(sibling, Comment)
            and (isinstance(sibling, Tag) or str(sibling).strip())
            for sibling in node.previous_siblings
        ):
            break
        node = node.parent
    return node


def _opening_text(node: Tag) -> str:
    """The first visible text block at a boundary, without consulting later prose."""
    if node.name in {"img", "svg", "object", "embed", "canvas", "script", "style"}:
        return ""
    if node.name in (*_HEADINGS, "p", "li"):
        return _heading_text(node)
    for child in node.children:
        if isinstance(child, Comment):
            continue
        text = (
            _opening_text(child)
            if isinstance(child, Tag)
            else PAGE_TOKEN_RE.sub("", str(child)).strip()
        )
        if text:
            return text
    return ""


def _navigation_boundary(node: Tag, aliases: list[NavigationEntry]) -> NavigationEntry:
    """Resolve labels sharing a source point, not an alleged TOC integrity error.

    Prefer a unique match to the printed opening label/designation. If navigation
    names disagree and the source cannot choose between them, leave the TOC title
    empty and let ordinary source-heading interpretation supply any metadata. All
    aliases remain in the immutable EPUB; they are not extra chapters or new fields.
    """
    first = aliases[0]
    if len({entry.title for entry in aliases}) == 1:
        return first

    def normalise(text):
        return re.sub(r"\s+", " ", text).strip().casefold()

    opening = _opening_text(node)
    source_number, source_title, source_part = _parse_designation(opening)

    def score(entry):
        if opening and entry.title == opening:
            return 4
        if opening and normalise(entry.title) == normalise(opening):
            return 3
        number, title, part = _parse_designation(entry.title)
        if part or source_part:
            return 0
        titles_match = bool(
            title and source_title and normalise(title) == normalise(source_title)
        )
        if (
            number
            and number == source_number
            and (titles_match or not title or not source_title)
        ):
            return 2
        return 1 if titles_match and (not number or not source_number) else 0

    best_score = max(map(score, aliases))
    best = [entry for entry in aliases if score(entry) == best_score]
    signatures = {
        (number, normalise(title or ""), part)
        for number, title, part in (_parse_designation(entry.title) for entry in best)
    }
    selected = best[0] if best_score and len(signatures) == 1 else None
    decision = (
        f"using source-matched label {selected.title!r}"
        if selected
        else "using source headings only"
    )
    warnings.warn(
        f"EPUB TOC aliases at {first.path}#{first.fragment}: {decision}; "
        f"navigation labels retained in EPUB: {[entry.title for entry in aliases]!r}",
        EpubExtractionWarning,
    )
    return selected or NavigationEntry(first.path, first.fragment, "", True)


def _logical_sections(
    body: Tag, filename: str, entries: list[NavigationEntry], *, notes=False
):
    """Split at evidenced logical DOM targets, preserving every intervening node.

    Package paths never collapse to basenames. A fragment starts at that actual
    element, not at the beginning of its containing spine file. Ancestor wrappers
    are cloned across boundaries so headings, lists and inline styles survive.
    """
    boundaries: dict[int, NavigationEntry] = {}
    targets: dict[int, tuple[Tag, list[NavigationEntry]]] = {}
    document_entries = [entry for entry in entries if entry.path == filename]
    for entry in document_entries:
        node = (
            body
            if not entry.fragment or body.get("id") == entry.fragment
            else body.find(id=entry.fragment)
        )
        if node is None:
            if entry.logical:
                raise EpubExtractionError(
                    f"Missing EPUB TOC target: {filename}#{entry.fragment}"
                )
            continue
        if entry.logical or _structural_kind(node):
            node = _boundary_start(node, body)
            targets.setdefault(id(node), (node, []))[1].append(entry)

    for node_id, (node, aliases) in targets.items():
        boundaries[node_id] = _navigation_boundary(node, aliases)

    if not notes:
        for node in body.find_all(True):
            kind = _structural_kind(node)
            text = _heading_text(node) if node.name in _HEADINGS else ""
            explicit = node.name in _HEADINGS and bool(
                _CHAPTER_PREFIX_RE.match(text) and _parse_designation(text)[0]
            )
            if not kind and not explicit:
                continue
            if id(node) in boundaries:
                continue
            # A chapter section and its opening Chapter N heading are one
            # boundary. A later explicit Chapter N heading can begin another.
            duplicate = False
            for ancestor in node.parents:
                existing = boundaries.get(id(ancestor))
                if existing is not None:
                    if (
                        not _parse_designation(existing.title)[2]
                        and _boundary_start(node, body) is ancestor
                    ):
                        duplicate = True
                    break
            if duplicate:
                continue
            if kind:
                heading = node.find(_HEADINGS)
                title = _heading_text(heading) if heading else ""
            else:
                title = text
            boundaries[id(node)] = NavigationEntry(
                filename, node.get("id", ""), title, True
            )

    # (entry, body, allow opening-heading metadata). A known subsection must not
    # become a new top-level section merely because it occupies its own file.
    sections = []
    ancestors: list[Tag] = []
    parents: list[Tag] = []
    fragment_soup = None

    def shallow(node):
        return fragment_soup.new_tag(
            node.name,
            namespace=node.namespace,
            nsprefix=node.prefix,
            attrs=dict(node.attrs),
        )

    def start(entry):
        nonlocal fragment_soup, parents
        fragment_soup = BeautifulSoup("<body/>", "lxml-xml")
        root = fragment_soup.body
        sections.append((entry, root, not document_entries or entry is not None))
        parents = [root]
        for ancestor in ancestors:
            clone = shallow(ancestor)
            # A true mid-paragraph boundary clones formatting, not the source
            # paragraph's start point. That coordinate was already emitted in
            # the preceding fragment. Leading targets are normalised above.
            clone.attrs.pop("data-kindle-position", None)
            clone.attrs.pop("id", None)
            parents[-1].append(clone)
            parents.append(clone)

    def visit(node):
        if not isinstance(node, Tag):
            parents[-1].append(copy(node))
            return
        entry = boundaries.get(id(node))
        if entry is not None:
            start(entry)
        clone = shallow(node)
        parents[-1].append(clone)
        parents.append(clone)
        ancestors.append(node)
        for child in node.children:
            visit(child)
        ancestors.pop()
        parents.pop()

    start(boundaries.get(id(body)))
    for child in body.children:
        visit(child)
    return sections


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
    description = _strip_html(_meta_first(book, "DC", "description"))
    identifier = _pick_identifier(book.get_metadata("DC", "identifier"))

    toc = _toc_entries(book)
    pages = _page_list_targets(book)
    resolver = _FootnoteResolver(book, pages)
    images: list[ExtractedImage] = []
    chapters: list[Chapter] = []
    max_number = 0
    index = 0
    remaining_page_files = {path for path, _ in pages}
    for item in _spine_documents(book):
        index += 1
        soup = BeautifulSoup(item.get_content(), "lxml-xml")
        _strip_navigation(soup)
        body = soup.find("body") or soup
        if resolver.prune(body, item.file_name):
            continue
        page_labels = _collect_pagebreaks(
            body,
            {
                fragment: label
                for (path, fragment), label in pages.items()
                if path == item.file_name
            },
        )
        remaining_page_files.discard(item.file_name)
        sections = _logical_sections(
            body, item.file_name, toc, notes=item.file_name in resolver.note_documents
        )
        for section_index, (entry, section, allow_body_metadata) in enumerate(sections):
            if section_index:
                index += 1
            body_title, body_number, _ = _analyse_body(section)
            if entry is not None and entry.title:
                toc_number, toc_clean, is_part = _parse_designation(entry.title)
                number = toc_number
                section_title = toc_clean or (entry.title if is_part else body_title)
                # An unnumbered TOC title is not evidence that its own printed
                # chapter heading lacks a number. Require agreement on title,
                # rather than borrowing a later endnote/subsection number.
                if (
                    not number
                    and not is_part
                    and body_number
                    and body_title == toc_clean
                ):
                    number = body_number
            elif entry is not None:
                # Conflicting navigation aliases cannot overrule an actual
                # printed heading, including chapter numbering that restarts.
                number, section_title = body_number, body_title
            elif allow_body_metadata:
                number = (
                    body_number
                    if body_number and int(body_number) > max_number
                    else None
                )
                section_title = body_title
            else:
                number, section_title = None, None
            _, _, markdown = _body_to_markdown(
                section,
                item.file_name,
                book,
                images,
                resolver,
                page_labels,
                strip_number=bool(number and number == body_number),
            )
            if not markdown and not (number or section_title):
                continue
            if number:
                max_number = max(max_number, int(number))
            chapters.append(
                Chapter(
                    index=index, title=section_title, markdown=markdown, number=number
                )
            )

    if remaining_page_files:
        raise EpubExtractionError(
            f"Page-list targets outside the readable spine: {sorted(remaining_page_files)!r}"
        )
    chapters = _coalesce_split_chapters(chapters)
    for chapter in chapters:
        markers = _redundant_designation_markers(chapter)
        if markers is not None:
            chapter.markdown = "\n".join(markers)
    _disambiguate_page_sequences(chapters)

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
