from email_shape import (
    Participant,
    parse_headers,
    render_message_annotation,
    drop_leading_heading,
    trim_raw_source_tail,
    segment_thread,
)

# The real shape of a WikiLeaks-published Podesta message (emailid/18724), which
# embeds a full RFC822 source block in the page.
PODESTA = """MIME-Version: 1.0
Received: by 10.25.155.143 with HTTP; Thu, 5 Mar 2015 15:38:14 -0800 (PST)
In-Reply-To: <003501d05799$c2415a20$46c40e60$@earthlink.net>
References: <003501d05799$c2415a20$46c40e60$@earthlink.net>
Date: Thu, 5 Mar 2015 18:38:14 -0500
Delivered-To: john.podesta@gmail.com
Message-ID: <CAE6FiQ_WT7@mail.gmail.com>
Subject: Re: Leslie Kean book comment
From: John Podesta <john.podesta@gmail.com>
To: Bob Fish <robertbfish@earthlink.net>

Thx for the note.
"""


def test_parse_headers_extracts_participants_and_authoritative_date():
    h = parse_headers(PODESTA)
    assert h.from_ == Participant(address="john.podesta@gmail.com", name="John Podesta")
    assert h.to == [Participant(address="robertbfish@earthlink.net", name="Bob Fish")]
    # the header Date is authoritative - this is the field the web path got
    # 15 years wrong by scraping a date-picker config off the page
    assert h.date.date().isoformat() == "2015-03-05"
    assert h.subject == "Re: Leslie Kean book comment"
    assert h.message_id == "<CAE6FiQ_WT7@mail.gmail.com>"
    assert h.in_reply_to == "<003501d05799$c2415a20$46c40e60$@earthlink.net>"
    assert h.references == ["<003501d05799$c2415a20$46c40e60$@earthlink.net>"]


def test_dkim_only_reported_when_the_source_carries_the_signature():
    # absent here - must never be inferred from "it came from a signed dump"
    assert parse_headers(PODESTA).dkim_signature_present is False
    signed = "DKIM-Signature: v=1; a=rsa-sha256; d=gmail.com\nFrom: a@b.c\n\nhi\n"
    assert parse_headers(signed).dkim_signature_present is True


def test_participants_dedupes_across_from_to_cc():
    raw = "From: A <a@x.com>\nTo: B <b@x.com>, A <a@x.com>\nCc: C <c@x.com>\n\nbody\n"
    addrs = [p.address for p in parse_headers(raw).participants()]
    assert addrs == ["a@x.com", "b@x.com", "c@x.com"]


def test_segment_thread_attributes_quoted_message_to_its_own_author():
    podesta = Participant(address="john.podesta@gmail.com", name="John Podesta")
    body = (
        "Thx for the note. Hard for me follow up at this moment.\n"
        "\n"
        'On Mar 5, 2015 6:08 PM, "Bob Fish" <robertbfish@earthlink.net> wrote:\n'
        "> John -\n"
        ">\n"
        "> I know you are busy as heck.\n"
    )
    segs = segment_thread(body, top_author=podesta)
    assert len(segs) == 2
    assert segs[0].quoted is False
    assert segs[0].author.address == "john.podesta@gmail.com"
    assert "Thx for the note" in segs[0].text
    # the crux: Fish's words must NOT be attributed to Podesta
    assert segs[1].quoted is True
    assert segs[1].author.address == "robertbfish@earthlink.net"
    assert segs[1].attributed_when == "Mar 5, 2015 6:08 PM"
    assert "I know you are busy" in segs[1].text
    assert ">" not in segs[1].text  # one level of quoting stripped


def test_segment_thread_handles_attribution_without_display_name():
    body = (
        "reply\n\nOn Tue, 1 Jan 2019 at 10:00, <someone@example.com> wrote:\n> older\n"
    )
    segs = segment_thread(body)
    assert segs[1].author.address == "someone@example.com"
    assert segs[1].text.strip() == "older"


def test_segment_thread_single_message_is_one_unquoted_segment():
    segs = segment_thread("just one message, no thread\n")
    assert len(segs) == 1
    assert segs[0].quoted is False


def _parse_annotation(ann: str):
    import yaml

    inner = ann[len("<!-- message: ") : -len(" -->")]
    return yaml.safe_load(inner)


def test_message_annotation_is_valid_yaml_with_freeform_date():
    # "Mar 5, 2015 6:08 PM" carries commas - unquoted it would split the flow
    # mapping into bogus entries.
    ann = render_message_annotation(
        2,
        Participant(address="robertbfish@earthlink.net", name="Bob Fish"),
        "Mar 5, 2015 6:08 PM",
        True,
    )
    got = _parse_annotation(ann)
    assert got["n"] == 2
    assert got["from"] == "Bob Fish <robertbfish@earthlink.net>"
    assert got["date"] == "Mar 5, 2015 6:08 PM"
    assert got["quoted"] is True


def test_message_annotation_keeps_iso_date_plain_and_parses():
    ann = render_message_annotation(1, None, "2015-03-05T18:38:14-05:00", False)
    assert '"2015-03-05' not in ann  # ISO stays a plain scalar
    assert _parse_annotation(ann)["quoted"] is False


def test_message_annotation_survives_a_comma_in_the_display_name():
    ann = render_message_annotation(
        1, Participant(address="j@x.com", name="Smith, John"), None, False
    )
    assert _parse_annotation(ann)["from"] == "Smith, John <j@x.com>"


def test_trim_raw_source_tail_cuts_the_indented_raw_block():
    # the raw block sits inside a <pre>, so it arrives INDENTED - anchoring hard
    # to the line start missed it and left the headers in the body
    text = (
        "Thx for the note.\n"
        "\n"
        "[Download raw source](/podesta-emails//get/18724)\n"
        "\n"
        "\t\t\tMIME-Version: 1.0\n"
        "Received: by 10.25.155.143 with HTTP\n"
        "From: John Podesta <john.podesta@gmail.com>\n"
    )
    got = trim_raw_source_tail(text)
    assert got.strip() == "Thx for the note."
    assert "MIME-Version" not in got
    # the publisher's download link is boundary furniture, not message content
    assert "Download raw source" not in got


def test_trim_raw_source_tail_leaves_an_ordinary_body_alone():
    text = "Just a normal message.\n\nRegards,\nBob\n"
    assert trim_raw_source_tail(text) == text


def test_segment_thread_strips_pre_indentation_from_first_segment():
    # the WikiLeaks bug: the first (unquoted) segment carried five leading tabs
    # that a block-level strip missed, rendering as an indented code block
    podesta = Participant(address="john.podesta@gmail.com", name="John Podesta")
    body = "\t\t\t\t\tThx for the note.\ncontact information.\n"
    segs = segment_thread(body, top_author=podesta)
    assert not segs[0].text.startswith(("\t", "    "))
    assert segs[0].text.splitlines()[0] == "Thx for the note."


def test_normalise_indent_leaves_sub_threshold_spaces():
    from email_shape import _normalise_indent

    # 1-3 leading spaces are below the code-block threshold - left alone
    assert _normalise_indent("  two spaces") == "  two spaces"
    assert _normalise_indent("    four spaces") == "four spaces"
    assert _normalise_indent("\ttab") == "tab"


def test_drop_leading_heading_removes_subject_echo():
    text = "## Re: Leslie Kean book comment\n\nThx for the note.\n"
    assert drop_leading_heading(text, "Re: Leslie Kean book comment") == (
        "Thx for the note.\n"
    )
    # a genuine first heading that is not the subject is kept
    kept = "## Some Other Heading\n\nbody\n"
    assert drop_leading_heading(kept, "Re: Leslie Kean book comment") == kept


def test_comment_close_in_display_name_cannot_truncate_annotation():
    import yaml

    # a display name carrying '-->' must not close the enclosing HTML comment
    evil = Participant(address="e@x.com", name="Bad --> guy")
    ann = render_message_annotation(1, evil, None, False)
    # the raw comment-close does not appear inside the annotation payload
    assert "-->" not in ann[: -len(" -->")]
    # a normal address bracket is left readable (only --> is escaped)
    assert "<e@x.com>" in ann
    # and it round-trips to the exact original bytes via a real YAML parser
    inner = ann[len("<!-- message: ") : -len(" -->")]
    assert yaml.safe_load(inner)["from"] == "Bad --> guy <e@x.com>"
