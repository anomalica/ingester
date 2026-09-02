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

from text_repair import rejoin_dropcaps  # re-exported for callers/tests

# Markdownify escapes underscores in plain text to prevent emphasis collisions, so
# token markers must be pure alphanumerics. Round-tripping through markdownify
# preserves these markers verbatim.
IMG_TOKEN_PREFIX = "ANOMALICAIMG"
IMG_TOKEN_SUFFIX = "IMGEND"
IMG_TOKEN_RE = re.compile(rf"{IMG_TOKEN_PREFIX}([0-9a-f]{{12}}){IMG_TOKEN_SUFFIX}")

REDACTION_TOKEN_PREFIX = "ANOMALICAREDACTED"
REDACTION_TOKEN_SUFFIX = "REDEND"
REDACTION_TOKEN_RE = re.compile(
    rf"{REDACTION_TOKEN_PREFIX}(\d+){REDACTION_TOKEN_SUFFIX}"
)

# Print-edition page markers from EPUB3 pagebreaks. The page label (title
# attribute) is alphanumeric in practice - Arabic digits or roman numerals for
# front matter - so it survives markdownify verbatim between the token affixes.
PAGE_TOKEN_PREFIX = "ANOMALICAPAGE"
PAGE_TOKEN_SUFFIX = "PGEND"
PAGE_TOKEN_RE = re.compile(rf"{PAGE_TOKEN_PREFIX}([0-9A-Za-z]+){PAGE_TOKEN_SUFFIX}")
# A page label worth emitting: Arabic digits or a roman numeral (front matter).
PAGE_LABEL_RE = re.compile(r"^[0-9A-Za-z]+$")

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
    alt: str | None = None


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
    ('Part One', 'II. Finding Our Liberty'), which carries a title but never a
    chapter number.
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
        # Uppercase Roman + '. ' is the part-divider convention in these books.
        return None, m.group(2).strip(), True
    m = _BARE_DESIGNATION_RE.match(t)
    if m and (n := _enum_to_int(m.group(1))) is not None and n <= _MAX_BARE_CHAPTER:
        return str(n), None, False
    return None, t, False


def _is_pure_designation(text: str) -> bool:
    """The whole heading is just a number ('Chapter One', 'ONE', '1') with no
    title of its own."""
    number, title, is_part = _parse_designation(text)
    return number is not None and title is None and not is_part


def _analyse_body(body) -> tuple[str | None, str | None, object | None]:
    """The chapter's title, its number, and the node to strip from the body.

    Finds the title heading (the first heading that is not itself just a number)
    and the chapter number, which may sit in that heading ('Chapter One: ...'),
    in a bare block right above it ('1' or 'ONE' styled as its own line), or be
    the heading itself when the chapter has no title of its own. The number's
    node is returned so the caller can drop it - otherwise it survives markdownify
    as an orphan '1'. A part divider ('PART ONE' above the title) yields no number.
    """
    blocks = [
        (tag, text)
        for tag in body.find_all(_HEADINGS + ("p",))
        if (text := tag.get_text(" ", strip=True))
    ]
    headings = [
        (i, tag, text) for i, (tag, text) in enumerate(blocks) if tag.name in _HEADINGS
    ]
    if not headings:
        return None, None, None

    # The title heading is the first heading that is neither a bare number nor a
    # part word; that heading may still carry its own number ('Chapter One: X').
    title_entry = next(
        (
            (i, tag, text)
            for i, tag, text in headings
            if not _is_pure_designation(text) and not _parse_designation(text)[2]
        ),
        None,
    )

    if title_entry is None:
        # Every heading is a bare number (e.g. 'ONE'..'SIX') - the number is the
        # chapter's identity and it has no separate title.
        i, tag, text = headings[0]
        number = _parse_designation(text)[0]
        return None, number, (tag if number else None)

    idx, _tag, text = title_entry
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


def _toc_titles(book: epub.EpubBook) -> dict[str, str]:
    """Map each document's filename to its title from the epub's navigation
    (toc.ncx / nav). The TOC is the authoritative source of chapter titles and
    carries the printed chapter number ('1. Shattered World'); many epubs style
    titles as `<p class="chaptername">` that the in-body h1-h3 scan cannot see."""
    titles: dict[str, str] = {}

    def key(href: str) -> str:
        return posixpath.basename(unquote(href).split("#", 1)[0])

    def walk(entries) -> None:
        for entry in entries:
            node, children = entry if isinstance(entry, (tuple, list)) else (entry, ())
            href = getattr(node, "href", None)
            title = getattr(node, "title", None)
            if href and title and title.strip():
                titles.setdefault(key(href), title.strip())
            if children:
                walk(children)

    try:
        walk(book.toc)
    except Exception:
        pass
    return titles


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
    (ignoring namespace) contains `needle` - handles `epub:type` however
    BeautifulSoup exposes it."""
    for key, value in tag.attrs.items():
        local = key.rsplit(":", 1)[-1].rsplit("}", 1)[-1]
        if local == local_name and needle in str(value):
            return True
    return False


def _is_noteref(a) -> bool:
    """A note reference: the little superscript that points at a footnote or
    endnote. Recognised by `epub:type="noteref"`, `role="doc-noteref"`, or the
    common plain form of a linked superscript pointing at an in-book anchor."""
    if _attr_contains(a, "type", "noteref") or _attr_contains(a, "role", "noteref"):
        return True
    href = (a.get("href") or "").strip()
    if "#" in href and not href.startswith(("http://", "https://", "mailto:")):
        return a.find("sup") is not None
    return False


def _note_content(element) -> str:
    """A footnote definition's text as a single markdown line, with its return
    arrow and leading marker number stripped. Parsed with the HTML parser so no
    XML declaration is prepended to the fragment."""
    frag = BeautifulSoup(str(element), "html.parser")
    for a in frag.find_all("a"):
        # An internal anchor in a note is its return arrow - remove it and its
        # text; an external link is a citation - keep it.
        if not (a.get("href") or "").startswith(("http://", "https://", "mailto:")):
            a.decompose()
    text = markdownify(str(frag), heading_style="ATX")
    text = re.sub(r"\s+", " ", text).strip()
    # Drop a markdown list bullet markdownify added, then the note's own leading
    # marker ('1', '1.', or an id-derived 'fn1').
    return re.sub(r"^(?:[-*]\s+)?(?:fn\s*)?\d+[.\s]*", "", text).strip()


# A notes document is spent - safe to drop - when this share of its anchored
# entries were pulled into citing chapters.
_NOTES_SPENT_SHARE = 0.8


class _FootnoteResolver:
    """Resolves note references to their definitions across the whole book.

    A note reference in one chapter points, by href, at a definition that often
    lives in a different spine document (a shared endnotes section). Numbers are
    assigned once, book-wide, so every `[^N]` is unique in the flattened record;
    the definition is placed with the chapter that cites it. Dedicated notes
    documents are recorded so the caller can drop them - their content has been
    pulled into the per-chapter definitions and would otherwise appear twice."""

    def __init__(self, book: epub.EpubBook) -> None:
        self.book = book
        self.counter = 0
        self.note_documents: set[str] = set()
        # Per notes document, the anchors whose definitions were pulled into a
        # citing chapter - what decides whether the document is spent.
        self.pulled: dict[str, set[str]] = {}
        self._soups: dict[str, BeautifulSoup | None] = {}

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
        target, _, fragment = href.partition("#")
        if not fragment:
            return label, ""
        target_file = _resolve_href(base_file, target) if target else base_file
        soup = self._soup(target_file)
        content = ""
        if soup is not None and (element := soup.find(id=fragment)) is not None:
            content = _note_content(element)
        if posixpath.basename(target_file) != posixpath.basename(base_file):
            name = posixpath.basename(target_file)
            self.note_documents.add(name)
            if content:
                self.pulled.setdefault(name, set()).add(fragment)
        return label, content

    def spent(self, filename: str) -> bool:
        """True when the notes document's definitions have (nearly) all been
        pulled into the chapters that cite them, so keeping the document would
        only repeat them. A book whose references are plain superscripts, with
        only a few linked, still needs its notes section - dropping it on the
        strength of those few lost 170 endnotes from one book."""
        pulled = self.pulled.get(filename, set())
        if not pulled:
            return False  # nothing was taken from it, so nothing would repeat
        soup = self._soup_by_name(filename)
        if soup is None:
            return False  # a document that cannot be read is never dropped
        anchors = {
            el.get("id")
            for el in soup.find_all(id=True)
            if el.get_text(" ", strip=True)
        }
        if not anchors:
            return False
        return len(anchors & pulled) >= _NOTES_SPENT_SHARE * len(anchors)

    def _soup_by_name(self, filename: str) -> BeautifulSoup | None:
        """The document whose file name (any directory prefix aside) is
        `filename` - note_documents holds bare names, while items in the
        package can sit under a prefix such as Text/."""
        for item in self.book.get_items():
            name = getattr(item, "file_name", "") or ""
            if posixpath.basename(name) == filename:
                return self._soup(name)
        return None


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
    for key, value in tag.attrs.items():
        local = key.rsplit(":", 1)[-1].rsplit("}", 1)[-1]
        text = str(value)
        if local == "type" and "pagebreak" in text:
            return True
        if local == "role" and "doc-pagebreak" in text:
            return True
    return False


def _pagebreak_label(tag) -> str | None:
    """The print-edition page label for a pagebreak - its `title` (e.g.
    title="308"), else a number in its id (id="page_308"), else its text."""
    title = (tag.get("title") or "").strip()
    if title:
        return title
    tag_id = (tag.get("id") or "").strip()
    match = re.search(r"([0-9]+|[ivxlcdmIVXLCDM]+)$", tag_id)
    if match:
        return match.group(1)
    text = tag.get_text(strip=True)
    return text or None


def _collect_pagebreaks(body) -> None:
    """Replace each EPUB pagebreak element with a page token carrying its
    print-edition label, so the marker survives markdownify (which mangles HTML
    comments) and expands to `<!-- printed_page: N -->` afterward. Pagebreaks
    with no usable alphanumeric label are dropped."""
    for tag in body.find_all(_is_pagebreak):
        label = _pagebreak_label(tag)
        if label and PAGE_LABEL_RE.match(label):
            tag.replace_with(f"\n\n{PAGE_TOKEN_PREFIX}{label}{PAGE_TOKEN_SUFFIX}\n\n")
        else:
            tag.decompose()


def _expand_page_tokens(md: str) -> str:
    return PAGE_TOKEN_RE.sub(lambda m: f"<!-- printed_page: {m.group(1)} -->", md)


# A pagebreak at the very start of a heading (the common per-chapter case)
# markdownifies inline: `## <!-- printed_page: 13 --> Chapter 2`.
_HEADING_PAGE_RE = re.compile(
    r"^(#{1,6})[ \t]+((?:<!-- printed_page: \S+ -->[ \t]*)+)(.*)$", re.MULTILINE
)


def _hoist_heading_page_markers(md: str) -> str:
    """Lift page markers that landed inside a heading onto their own lines
    before it, keeping the own-line convention (`<!-- printed_page: 13 -->` then
    `## Chapter 2`). A heading that was only a pagebreak yields just the marker."""

    def repl(match: re.Match) -> str:
        markers = re.findall(r"<!-- printed_page: \S+ -->", match.group(2))
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
    title, number, number_tag = _analyse_body(body)
    if number_tag is not None:
        number_tag.decompose()
    footnotes = _collect_footnotes(body, chapter_file, resolver)
    _strip_internal_anchors(body)
    _collect_images(body, chapter_file, book, images)
    _collect_pagebreaks(body)
    _replace_redactions_in_soup(body)
    md = markdownify(str(body), heading_style="ATX", strip=["script", "style"])
    md = rejoin_dropcaps(md)
    md = _expand_redaction_tokens(md)
    md = _expand_page_tokens(md)
    md = _expand_footnote_tokens(md)
    md = _hoist_heading_page_markers(md)
    md = "\n".join(line.rstrip() for line in md.splitlines())
    while "\n\n\n" in md:
        md = md.replace("\n\n\n", "\n\n")
    md = md.strip()
    if footnotes:
        md = f"{md}\n\n" + "\n".join(footnotes)
    return title, number, md


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
    description = _strip_html(_meta_first(book, "DC", "description"))
    identifier = _pick_identifier(book.get_metadata("DC", "identifier"))

    toc = _toc_titles(book)
    resolver = _FootnoteResolver(book)
    images: list[ExtractedImage] = []
    chapters: list[Chapter] = []
    chapter_files: list[str] = []
    max_number = 0
    for index, item in enumerate(_spine_documents(book), start=1):
        body_title, body_number, markdown = _xhtml_to_markdown(
            item.get_content(), item.file_name, book, images, resolver
        )
        markdown = _expand_image_tokens(markdown, images)
        if not markdown:
            continue
        toc_title = toc.get(posixpath.basename(item.file_name))
        # The TOC is authoritative: when it names a section, its number (or lack
        # of one) is trusted, so a back-matter section it titles "Notes" is not
        # given a chapter number just because the body groups notes by chapter.
        # The body number is used only where the TOC has no entry at all, as with
        # a book whose chapters are bare 'ONE'..'SIX' the TOC never lists.
        if toc_title:
            toc_number, toc_clean, is_part = _parse_designation(toc_title)
            number = None if is_part else toc_number
            section_title = toc_clean or body_title
        else:
            # A section the TOC does not list takes its number from the body, but
            # only if it continues the chapter sequence upward. That keeps a book
            # whose chapters are bare 'ONE'..'SIX' while rejecting back-matter
            # (endnotes grouped "5. Cognitive Ease") that reuses chapter numbers.
            number = (
                body_number if body_number and int(body_number) > max_number else None
            )
            section_title = body_title
        if number:
            max_number = max(max_number, int(number))
        chapters.append(
            Chapter(index=index, title=section_title, markdown=markdown, number=number)
        )
        chapter_files.append(posixpath.basename(item.file_name))

    # Drop a dedicated notes document once its definitions have been pulled into
    # the chapters that cite them, so the raw section does not repeat them. One
    # that is mostly unreferenced stays: its notes exist nowhere else.
    chapters = [
        chapter
        for chapter, filename in zip(chapters, chapter_files)
        if filename not in resolver.note_documents or not resolver.spent(filename)
    ]

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
