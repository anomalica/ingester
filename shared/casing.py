"""Post-transcription casing normalisation.

Whisper lower-cases proper nouns, acronyms and the pronoun "I" ("i saw a ufo").
This restores the canonical casing of a CURATED list of terms (loaded from
``casing_terms.yaml``), applied as whole-word (word-boundary), case-insensitive
replacements. It is a CASE-ONLY transform: it never adds, removes or reorders
characters beyond changing their case, and never touches a substring inside another
word ("ufology" is left alone). That property is what lets a caller re-split a cased
line back onto per-word timestamps safely - the word count cannot change.

SAFETY - why the list is curated, not clever: there is no reliable way to tell "the
US" from the pronoun "us", or the month "May" from "it may be", by pattern alone. So
the list must hold ONLY terms that are unambiguously the proper-noun/acronym form in
this corpus. Ambiguous terms live under ``candidates:`` in the YAML, which is NOT
loaded, until a human promotes them. Word-boundary matching plus that curation are
the whole safety model; the code guesses nothing.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

import yaml

_DEFAULT_TERMS = Path(__file__).with_name("casing_terms.yaml")


def load_rules(path: str | Path | None = None) -> dict[str, str]:
    """Load the curated terms into a ``{lowercase_key: canonical}`` map.

    Reads canonical forms grouped under ``terms:`` (the category names are only for
    the human curator - every value is flattened and applied). A ``candidates:``
    block is IGNORED: those are ambiguous terms parked for review, never applied.
    A missing or empty file yields an empty map (an identity caser)."""
    path = Path(path) if path else _DEFAULT_TERMS
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    groups = data.get("terms", {})
    collected: list[str] = []
    if isinstance(groups, dict):
        for value in groups.values():
            if isinstance(value, list):
                collected.extend(str(t) for t in value)
    elif isinstance(groups, list):  # a flat list is accepted too
        collected.extend(str(t) for t in groups)
    rules: dict[str, str] = {}
    for term in collected:
        term = term.strip()
        if term:
            rules[term.lower()] = term
    return rules


def build_caser(rules: dict[str, str]) -> Callable[[str], str]:
    """Return a function that restores the ruled terms' canonical casing in a text.

    Matches each term whole-word (``\\b`` boundaries) and case-insensitively, longest
    term first so a multi-word term wins over its own first word. Case-only: the
    returned text has the same characters and word count, only re-cased. An empty
    rule map returns an identity function."""
    if not rules:
        return lambda text: text
    keys = sorted(rules, key=len, reverse=True)
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(k) for k in keys) + r")\b", re.IGNORECASE
    )

    def _replace(match: re.Match) -> str:
        # Look the matched span up by its lower-cased form; leave it untouched if
        # (defensively) it is not a known key.
        return rules.get(match.group(0).lower(), match.group(0))

    def caser(text: str) -> str:
        return pattern.sub(_replace, text)

    return caser


def default_caser() -> Callable[[str], str]:
    """The caser built from the bundled ``casing_terms.yaml``."""
    return build_caser(load_rules())
