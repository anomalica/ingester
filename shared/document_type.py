"""Two facts that `source_type` used to conflate.

`source_type` mixed HOW a thing reached us (video, audio, web) with WHAT it is
(ebook, document) and WHAT FILE it is (pdf, image). This module splits the last
two apart:

- `file_format` - the format of the archived original we actually hold (pdf,
  epub, html, jpg, opus). DERIVED from the file, never editable, ALWAYS present:
  the file is what it is. For audio/video we keep only the extracted audio, so
  file_format is `opus` there - it describes the file we hold, not the medium the
  source happened to be.

- `document_type` - what the work IS (a book, a paper, an interview). A CLOSED
  list, EDITABLE by a reviewer, the primary type shown for a record - and EMITTED
  ONLY WHERE THE ARTEFACT STATES ITS OWN FORM. The test is whether it states its
  form, not whether it resembles one: "Full Documentary", "DEBRIEFED", "Ep. N",
  "... Incident Report", "Statement to Congress" are the artefact naming itself.
  Everything else is left ABSENT. Absence is the not-evidenced marker here as
  everywhere else in this format, and it is the useful state: a missing
  document_type invites a human to look, a wrong one does not. A guessed value
  must never share the field with a derived one, or a consumer holds two classes
  of value under one name.

`source_type` stays for now; consumers migrate to these two, then it is removed.
"""

from __future__ import annotations

import re

# The closed set. Additions go through an ingest-format.md spec change, never
# free text - a free-text field stops being queryable, and the point of the
# field is that a reader can tell a peer-reviewed paper from a podcast.
DOCUMENT_TYPES: tuple[str, ...] = (
    # text / primary records
    "book",
    "paper",
    "report",
    "article",
    "letter",
    "email",
    "statement",
    "form",
    "transcript",
    "slide",
    # spoken / broadcast
    "interview",
    "documentary",
    "podcast",
    "lecture",
    "broadcast",
    "recording",
)

# Audio/video: ordered, first match wins. ONLY patterns that cannot reasonably
# mean something else - the title STATING its form. None of these can be a verb
# or an incidental word, so a match is derivation, not inference. No match ->
# None (absent), never a neutral guess.
_AV_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("interview", re.compile(r"DEBRIEFED|Interviewed by", re.I)),
    ("documentary", re.compile(r"Full Documentary|\(Documentary", re.I)),
    ("podcast", re.compile(r"Podcast|\bEp\.?\s*\d+", re.I)),
    ("broadcast", re.compile(r"press conference", re.I)),
)

# Text documents (pdf, image): the same test. A form-word must be anchored as a
# NOUN naming the document, not used incidentally - "Tajik Air Pilots Report a
# UFO" has "Report" as a verb and states no form, so it stays absent. `report`
# therefore matches only trailing / "Report:" / "Report No." / leading, plus the
# fixed compounds; the others are unambiguous as bare words.
_REPORT = re.compile(
    r"\bReport\s*$|\bReport\s*:|\bReport\s+No\.|^Report\s+"
    r"|Incident Report|Sighting Report|Conference Report",
    re.I,
)
_TEXT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("slide", re.compile(r"\bslide\b", re.I)),
    ("form", re.compile(r"\bform\b", re.I)),
    ("transcript", re.compile(r"\btranscript(ion)?\b", re.I)),
    ("letter", re.compile(r"\b(letter|correspondence)\b", re.I)),
    ("statement", re.compile(r"\bstatement\b", re.I)),
    ("report", _REPORT),
)


def _first_match(
    title: str, patterns: tuple[tuple[str, re.Pattern[str]], ...]
) -> str | None:
    for label, pattern in patterns:
        if pattern.search(title or ""):
            return label
    return None


def classify_av(title: str) -> str | None:
    """document_type an audio/video title STATES, or None to leave it absent."""
    return _first_match(title, _AV_PATTERNS)


def classify_text(title: str) -> str | None:
    """document_type a document title STATES, or None to leave it absent."""
    return _first_match(title, _TEXT_PATTERNS)


def derive_document_type(source_type: str, title: str = "") -> str | None:
    """The document_type derivable from a record without inference, or None.

    None is a first-class answer: the artefact does not state its form, so the
    field is left absent for a human to fill. There is no blanket per-source_type
    default - a default is a guess, and a guessed value indistinguishable from a
    derived one is what this field is built to avoid. (Web email is derived from
    its headers by the webpage handler, not from the title, so it is not here.)"""
    if source_type in ("audio", "video"):
        return classify_av(title)
    if source_type in ("pdf", "image"):
        return classify_text(title)
    return None


# One token per real format. opus-in-ogg has been written as both `opus` (197
# records) and `ogg` (15) for the same thing - the inconsistency this field
# exists to remove - so collapse it, and normalise the other synonyms too.
_FILE_FORMAT_ALIASES = {
    "ogg": "opus",
    "oga": "opus",
    "jpeg": "jpg",
    "htm": "html",
}


def normalise_file_format(ext: str | None) -> str | None:
    """A bare format token from an extension or codec, or None if there is none."""
    if not ext:
        return None
    token = ext.strip().lstrip(".").lower()
    if not token:
        return None
    return _FILE_FORMAT_ALIASES.get(token, token)
