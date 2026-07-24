"""Email as a record SHAPE, normalised from any acquisition path.

`source_type` says how a thing was acquired (web, pdf, eml); this module says
what it IS. The same email can arrive as a WikiLeaks HTML page, a raw .eml, or
embedded inside a FOIA PDF - all three normalise here, so the shape is reachable
from every handler rather than living in one of them.

Everything is deterministic stdlib parsing: no model call, no spend.
"""

from __future__ import annotations

import email
import re
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import getaddresses, parsedate_to_datetime


@dataclass
class Participant:
    """A named party on an email - the graph edge an email carries."""

    address: str
    name: str | None = None

    def rendered(self) -> str:
        return f"{self.name} <{self.address}>" if self.name else self.address


@dataclass
class EmailHeaders:
    from_: Participant | None = None
    to: list[Participant] = field(default_factory=list)
    cc: list[Participant] = field(default_factory=list)
    date: datetime | None = None
    subject: str | None = None
    message_id: str | None = None
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)
    # Only true when the SOURCE actually carried a DKIM-Signature header. Never
    # inferred from provenance ("it's from a signed dump") - claiming a message
    # is cryptographically verified when we hold no signature would be a
    # fabricated evidence property.
    dkim_signature_present: bool = False

    def participants(self) -> list[Participant]:
        seen: set[str] = set()
        out: list[Participant] = []
        for p in ([self.from_] if self.from_ else []) + self.to + self.cc:
            key = p.address.casefold()
            if key and key not in seen:
                seen.add(key)
                out.append(p)
        return out


def _participants(raw: str | None) -> list[Participant]:
    if not raw:
        return []
    out: list[Participant] = []
    for name, addr in getaddresses([raw]):
        addr = (addr or "").strip()
        if not addr:
            continue
        out.append(Participant(address=addr, name=(name or "").strip() or None))
    return out


def _first(raw: str | None) -> Participant | None:
    got = _participants(raw)
    return got[0] if got else None


def parse_headers(raw_message: str) -> EmailHeaders:
    """Parse an RFC822 message into the normalised header shape.

    Accepts a full message (headers + body) or a header block alone.
    """
    msg = email.message_from_string(raw_message)
    date = None
    if msg.get("Date"):
        try:
            date = parsedate_to_datetime(msg["Date"])
        except (TypeError, ValueError):
            date = None
    refs = (msg.get("References") or "").split()
    return EmailHeaders(
        from_=_first(msg.get("From")),
        to=_participants(msg.get("To")),
        cc=_participants(msg.get("Cc")),
        date=date,
        subject=(msg.get("Subject") or "").strip() or None,
        message_id=(msg.get("Message-ID") or "").strip() or None,
        in_reply_to=(msg.get("In-Reply-To") or "").strip() or None,
        references=[r for r in refs if r.strip()],
        dkim_signature_present=msg.get("DKIM-Signature") is not None,
    )


# "On Mar 5, 2015 6:08 PM, "Bob Fish" <robertbfish@earthlink.net> wrote:" and the
# common variants (no display name, angle-bracketed address only, wrapped lines).
_ATTRIBUTION_RE = re.compile(
    r"^\s*On\s+(?P<when>.{3,80}?),?\s*"
    r"(?:\"(?P<qname>[^\"]+)\"|(?P<name>[^<>\"]{1,60}?))?\s*"
    r"<(?P<addr>[^>\s]+@[^>\s]+)>\s*wrote:\s*$",
    re.IGNORECASE,
)


@dataclass
class Segment:
    """One message within a thread: who wrote it and whether it was quoted."""

    text: str
    author: Participant | None
    quoted: bool
    attributed_when: str | None = None


def _dequote(lines: list[str]) -> list[str]:
    """Strip one level of '>' quoting."""
    out = []
    for ln in lines:
        stripped = ln.lstrip()
        if stripped.startswith(">"):
            body = stripped[1:]
            out.append(body[1:] if body.startswith(" ") else body)
        else:
            out.append(ln)
    return out


def segment_thread(body: str, top_author: Participant | None = None) -> list[Segment]:
    """Split a thread into attributed messages.

    An email body is usually the newest message followed by an attribution line
    ("On <date>, <person> wrote:") and the previous message quoted with '>'.
    Without this split a two-party exchange reads as one blob and a model will
    attribute the quoted party's words to the sender. Each returned segment
    carries its own author, so attribution survives into the record.
    """
    lines = body.splitlines()
    segments: list[Segment] = []
    current: list[str] = []
    author = top_author
    quoted = False
    when = None

    for ln in lines:
        m = _ATTRIBUTION_RE.match(ln)
        if m:
            if current and "".join(current).strip():
                segments.append(
                    Segment(
                        text="\n".join(current).strip(),
                        author=author,
                        quoted=quoted,
                        attributed_when=when,
                    )
                )
            name = m.group("qname") or m.group("name")
            author = Participant(
                address=m.group("addr"), name=(name or "").strip() or None
            )
            when = (m.group("when") or "").strip() or None
            current = []
            quoted = True
            continue
        current.append(ln)

    if current and "".join(current).strip():
        segments.append(
            Segment(
                text="\n".join(current).strip(),
                author=author,
                quoted=quoted,
                attributed_when=when,
            )
        )

    # A quoted segment arrives '>'-prefixed; strip one level so the prose is
    # readable and the quoting is expressed by the annotation, not punctuation.
    for seg in segments:
        if seg.quoted:
            seg.text = "\n".join(_dequote(seg.text.splitlines())).strip()
    return segments
