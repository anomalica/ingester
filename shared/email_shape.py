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


def _normalise_indent(text: str) -> str:
    """Drop leading indentation that is an extraction artefact, not content.

    A message published inside a <pre> arrives indented, and in CommonMark a
    leading tab or 4+ leading spaces is an indented code block - the workbench
    prose pane then renders those lines in a monospace box with overflow and
    drops the un-indented continuation into its own paragraph. A block-level
    strip() misses this because the indentation is mid-block. Any line that
    starts with a tab or 4+ spaces (the code-block triggers) is left-stripped;
    1-3 leading spaces are below the threshold and left alone.

    KNOWN LIMITATION (email prose is the common case, so this is the right
    default): legitimately-indented content in a technical email - a pasted code
    snippet, a log excerpt, an ASCII table - is flattened with no trace. There is
    no clean discriminator: "de-indent only a uniform-prefix block" would keep
    code but miss the reported bug (only the first line carried the tabs, the
    wrap continuation had none), and "flatten only isolated indented lines" just
    trades one failure mode for another. Carried deliberately, not overlooked.
    """
    out = []
    for ln in text.splitlines():
        if ln[:1] == "\t" or re.match(r"^ {4,}", ln):
            ln = ln.lstrip(" \t")
        out.append(ln)
    return "\n".join(out)


_HEADING_RE = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*$")


def drop_leading_heading(text: str, title: str | None) -> str:
    """Drop a leading markdown heading that merely repeats the message subject.

    A page publishing an email renders the subject as the body's first heading;
    with the subject already in frontmatter that heading is page furniture, not
    message content. Only strips when it matches the title (normalised), so a
    genuine first heading is left untouched.
    """
    if not title:
        return text
    stripped = text.lstrip("\n")
    m = _HEADING_RE.match(stripped.split("\n", 1)[0])
    if (
        m
        and re.sub(r"\s+", " ", m.group(1)).strip().casefold()
        == re.sub(r"\s+", " ", title).strip().casefold()
    ):
        rest = stripped.split("\n", 1)
        return rest[1].lstrip("\n") if len(rest) > 1 else ""
    return text


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
    # Every segment (not only the quoted ones) is de-indented: <pre> indentation
    # survives the block-level strip mid-segment and renders as a code block.
    for seg in segments:
        if seg.quoted:
            seg.text = "\n".join(_dequote(seg.text.splitlines()))
        seg.text = _normalise_indent(seg.text).strip()
    return segments


# --- acquisition-side helpers -------------------------------------------------

_PRE_RE = re.compile(r"<pre[^>]*>(.*?)</pre>", re.S | re.I)
_HEADER_HINT_RE = re.compile(r"^(From|Date|Message-ID):", re.M | re.I)


def extract_embedded_rfc822(html: str) -> str | None:
    """The raw RFC822 message embedded in an HTML page, if there is one.

    Publishers of email dumps (WikiLeaks among them) render the readable message
    and also embed the original source block verbatim. That block is the
    authoritative header source - the rendered page's own date/byline furniture
    is not. Returns the first <pre> that actually parses as a message.
    """
    import html as _html

    for raw in _PRE_RE.findall(html or ""):
        text = _html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()
        if not _HEADER_HINT_RE.search(text):
            continue
        msg = email.message_from_string(text)
        if msg.get("From") and (msg.get("Date") or msg.get("Message-ID")):
            return text
    return None


def _yaml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


_PLAIN_FLOW_SCALAR_RE = re.compile(r"^[A-Za-z0-9._:+-]+$")


def _flow_scalar(value: str) -> str:
    """A value safe to write unquoted inside a YAML flow mapping, else quoted."""
    return value if _PLAIN_FLOW_SCALAR_RE.match(value) else _yaml_quote(value)


def render_email_frontmatter(h: EmailHeaders) -> list[str]:
    """The `email:` frontmatter block.

    Carries only what the flat fields cannot: addresses, to/cc, subject and the
    threading ids. `date_published` and `creators` are written from the header
    by the caller and are deliberately NOT repeated here.
    """
    lines = ["email:"]
    if h.from_:
        lines.append(f"  from: {_yaml_quote(h.from_.rendered())}")
    for key, people in (("to", h.to), ("cc", h.cc)):
        if people:
            lines.append(f"  {key}:")
            lines.extend(f"    - {_yaml_quote(p.rendered())}" for p in people)
    if h.subject:
        lines.append(f"  subject: {_yaml_quote(h.subject)}")
    if h.message_id:
        lines.append(f"  message_id: {_yaml_quote(h.message_id)}")
    if h.in_reply_to:
        lines.append(f"  in_reply_to: {_yaml_quote(h.in_reply_to)}")
    if h.references:
        lines.append("  references:")
        lines.extend(f"    - {_yaml_quote(r)}" for r in h.references)
    if h.dkim_signature_present:
        # Present only when the source carried the signature. Its ABSENCE means
        # this copy is unsigned, never that verification failed.
        lines.append("  dkim_signature_present: true")
    return lines


def render_message_annotation(
    n: int, author: Participant | None, when: str | None, quoted: bool
) -> str:
    """One block annotation per thread segment (record-format `message:`).

    A single YAML mapping rather than loose keys, so a parser can never read a
    missing key as a misplaced one. `quoted` is load-bearing: every claim drawn
    from a quoted segment belongs to THAT segment's author, never the sender of
    the containing message.
    """
    parts = [f"n: {n}"]
    if author:
        parts.append(f"from: {_yaml_quote(author.rendered())}")
    if when:
        # This is a YAML FLOW mapping, so any value carrying a comma or brace
        # must be quoted or it splits into bogus entries. An ISO timestamp is a
        # safe plain scalar; a free-form attribution date ("Mar 5, 2015 6:08 PM")
        # is not.
        parts.append(f"date: {_flow_scalar(when)}")
    parts.append(f"quoted: {'true' if quoted else 'false'}")
    return "<!-- message: {" + ", ".join(parts) + "} -->"


def render_thread_body(segments: list[Segment], top_when: str | None = None) -> str:
    """The record body: each message annotated with its own author and quoting."""
    out: list[str] = []
    for i, seg in enumerate(segments, 1):
        when = top_when if (i == 1 and not seg.quoted) else seg.attributed_when
        out.append(render_message_annotation(i, seg.author, when, seg.quoted))
        out.append("")
        out.append(seg.text)
        out.append("")
    return "\n".join(out).strip() + "\n"


# The first line of a raw header block. Leading whitespace is allowed because the
# block is usually indented inside a <pre>, and anchoring hard to the line start
# missed it - the trim then fired later and left the headers behind.
_ONLY_LINK_RE = re.compile(r"^\[[^\]]*\]\([^)]*\)$")

_RAW_TAIL_RE = re.compile(
    r"^[ \t]*(MIME-Version:\s|Delivered-To:\s|Received:\s+(by|from)\s"
    r"|Content-Type:\s*multipart/)",
    re.M,
)


def trim_raw_source_tail(text: str) -> str:
    """Cut the raw MIME source block off the end of an extracted email body.

    A page that publishes an email renders the readable message and then repeats
    it as raw source. The rendered copy is the record body; the raw block is
    provenance and already lives in the archived original via source_hash.
    Leaving it in duplicates the whole message and feeds boundary markers and
    quoted-printable escapes to every downstream consumer.

    Trimming the extracted TEXT rather than removing the block from the HTML is
    deliberate: dropping the <pre> changes what the extractor scores as the main
    content and it selects the site's boilerplate instead.
    """
    m = _RAW_TAIL_RE.search(text)
    if not m:
        return text
    lines = text[: m.start()].rstrip().splitlines()
    # Publishers put a "download the original" link immediately before the raw
    # block. That is furniture sitting on the boundary, not message content.
    while lines and (not lines[-1].strip() or _ONLY_LINK_RE.match(lines[-1].strip())):
        lines.pop()
    return "\n".join(lines).rstrip() + "\n"
