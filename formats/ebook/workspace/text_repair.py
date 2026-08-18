"""Pure text repairs shared by extraction and the post-ingestion inspection pass.

No EPUB or model dependency - just string transforms - so the inspection/repair
tooling can reuse them without pulling in the extractor's parser.
"""

from __future__ import annotations

import re

# A drop cap that markdownify split onto its own line. EPUBs style a chapter's
# first letter as a large decorative capital in its own element, so 'While'
# extracts as 'W' alone then 'hile...'. A single capital alone on a line
# immediately followed by a line beginning lowercase is that split; join them
# WITHOUT a space, since the capital is the first letter of that word.
#
# Deliberately EXCLUDES 'A' and 'I' (the only single-letter English words): for
# those the capital may be a whole word rather than a first letter - 'I' then
# 'was' is "I was" (a space), but 'I' then 'ndridi' is "Indridi" (none), and 'I'
# then 'n' is "In". Telling those apart needs to know whether the continuation is a
# whole word or a fragment, which is the inspection layer's job, not a regex's.
# Joining them here would produce 'Iwas'. Every other capital is only ever a word's
# first letter, so the join is unambiguous and never wrong.
_DROPCAP_SPLIT_RE = re.compile(r"^([B-HJ-Z])\n(?=[a-z])", re.MULTILINE)


def rejoin_dropcaps(md: str) -> str:
    """Rejoin drop-cap first letters split onto their own line, for the capitals
    where the join is unambiguous (every letter except A and I)."""
    return _DROPCAP_SPLIT_RE.sub(r"\1", md)
