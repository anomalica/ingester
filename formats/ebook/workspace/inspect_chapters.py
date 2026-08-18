"""Post-ingestion chapter-boundary inspection for book records.

Extraction damage clusters at the START of a chapter: the decorative drop-cap that
came out as a lone letter, a mangled page/marker comment, a heading that does not
match its number, a first sentence that does not begin cleanly. So this does NOT
sweep the whole book - it looks only at the boundary region of each chapter, which
is where the problems are and where a false positive would do the least reaching.

The model INSPECTS and returns machine-applicable findings (a type, a location, and
where relevant the corrected fragment); it never rewrites the body. Deterministic
fixes are applied by code from those findings, so no model-generated prose enters
the record - the reason we can use inspection without the watermarking and drift
that rewriting a book would bring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The heading that opens a chapter, and how many lines past it count as the
# boundary region worth inspecting (the drop cap, first marker, first sentence).
_HEADING_RE = re.compile(r"^#{1,6}\s+\S")
_BOUNDARY_LINES = 12

INSPECT_PREAMBLE = (
    "You inspect the opening lines of a book chapter extracted from an EPUB for "
    "mechanical extraction artefacts. Report only concrete defects; do NOT rewrite, "
    "improve, or paraphrase the prose. Look for:\n"
    "1. dropcap_split - the chapter's first letter left on its own line. Decide the "
    "correct join by whether the continuation is a whole word or a word-fragment: "
    "'W'+'hile' -> 'While' (no space); 'I'+'was' -> 'I was' (a space, 'I' is a whole "
    "word); 'I'+'ndridi' -> 'Indridi' (no space, a name); 'I'+'n' -> 'In'. Put the "
    "correctly joined text in `corrected`.\n"
    "2. marker_malformed - a printed_page/file_page HTML comment that is broken or "
    "mid-line.\n"
    "3. title_problem - the heading is missing, is only a number, or looks wrong.\n"
    "4. sentence_start - the first sentence does not begin with a capital or is "
    "visibly garbled.\n"
    "Return JSON: an `issues` array (empty if the opening is clean). Each issue has "
    "`type`, `detail`, and `corrected` when there is a concrete fix."
)

INSPECT_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "dropcap_split",
                            "marker_malformed",
                            "title_problem",
                            "sentence_start",
                            "other",
                        ],
                    },
                    "detail": {"type": "string"},
                    "corrected": {"type": "string"},
                },
                "required": ["type", "detail"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["issues"],
    "additionalProperties": False,
}


@dataclass
class ChapterBoundary:
    index: int
    heading: str
    line_no: int  # 1-based line of the heading in the record
    region: str  # heading + the first _BOUNDARY_LINES non-empty content lines


def find_boundaries(body: str) -> list[ChapterBoundary]:
    """The opening region of each chapter, keyed off headings. Front matter before
    the first heading is not a chapter and is skipped."""
    lines = body.split("\n")
    heads = [i for i, ln in enumerate(lines) if _HEADING_RE.match(ln)]
    boundaries = []
    for n, i in enumerate(heads, start=1):
        end = i + 1
        taken = 0
        while end < len(lines) and taken < _BOUNDARY_LINES:
            if lines[end].strip():
                taken += 1
            if _HEADING_RE.match(lines[end]) and end != i:
                break
            end += 1
        region = "\n".join(lines[i:end]).strip()
        boundaries.append(
            ChapterBoundary(
                index=n, heading=lines[i].strip(), line_no=i + 1, region=region
            )
        )
    return boundaries


# A drop-cap split still present at a boundary (any capital, INCLUDING A/I, since
# the deterministic pass leaves A/I for judgement). Used to pre-filter: a boundary
# with no split and a well-formed marker need not cost a model call.
_ANY_DROPCAP_RE = re.compile(r"(?m)^[A-Z]\n[a-z]")
_MARKER_RE = re.compile(r"<!--\s*(?:printed_page|file_page):")


def needs_inspection(region: str) -> bool:
    """Cheap pre-filter: only spend a model call where a deterministic scan already
    smells a problem (an unresolved drop-cap split, or a marker fragment that is not
    a well-formed comment)."""
    if _ANY_DROPCAP_RE.search(region):
        return True
    if "printed_page" in region or "file_page" in region:
        if not _MARKER_RE.search(region):
            return True
    return False


def inspect_boundary(region: str, call) -> list[dict]:
    """Inspect one boundary region. `call` is an injected model function
    (preamble, document, task, schema) -> json string, so this stays testable and
    transport-agnostic."""
    import json

    task = (
        "Inspect the chapter opening below and report mechanical artefacts only.\n\n"
        f"---\n{region}\n---"
    )
    raw = call(INSPECT_PREAMBLE, region, task, INSPECT_SCHEMA)
    try:
        return json.loads(raw).get("issues", [])
    except (json.JSONDecodeError, AttributeError):
        return [
            {"type": "other", "detail": f"unparseable inspector output: {raw[:120]}"}
        ]
