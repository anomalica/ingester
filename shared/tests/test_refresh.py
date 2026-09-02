import hashlib
import json
import re

from refresh import (
    carry_review_work,
    port_irrelevant_markers,
    refresh_record,
    restamp,
    transplant_image_files,
    words_gone,
)

OLD_BODY = """Photo by Heidi Kaden on Unsplash

Written by Christopher Sharp - 24 April 2026

**Burlison has said** he has

that the death appears

“grave concerns”, suggesting the officer may have been silenced.

<!--
image:
  file: 5f2f548aea81.jpg
  caption: "A photo"
-->

<!-- irrelevant: start -->

Love our content and wish to support the website?

You can now become a Patron: Liberation Times | Patreon

<!-- irrelevant: end -->
"""

FRESH_BODY = """Photo by [Heidi Kaden](https://unsplash.com/x) on Unsplash

Written by [Christopher Sharp](https://twitter.com/x) - 24 April 2026

Burlison has said he has “grave concerns” that the death appears “suspicious”, suggesting the officer may have been silenced.

<!--
image:
  caption: "A photo"
-->
"""

FRONTMATTER = """schema: anomalica/record/1
title: "Late Officer"
date_published: 2026-04-24
source_type: web
file_format: html
source_url: https://www.liberationtimes.com/home/late-officer
publisher: "Liberation Times"
content_hash: sha256:{h}
source_hash: sha256:{sh}
date_accessed: 2026-08-13T00:48:13+00:00
date_extracted: 2026-08-13T00:49:01+00:00
copyright:
  status: publicly_accessible
processing:
  handler: webpage
  version: d4f18fe
  pipeline_version: 2
  tools:
    - name: trafilatura
      version: "2.1.0"
      role: extraction
      provider: local
quality:
  replacement_chars: 0
  substitution_score: 0.0"""


def _store(tmp_path, body=OLD_BODY, reviewed=False, source_bytes=b"<html>page</html>"):
    store = tmp_path / "output" / "store"
    store.mkdir(parents=True)
    h = "a" * 64
    sh = hashlib.sha256(source_bytes).hexdigest()
    record = store / f"{h}.md"
    record.write_text(f"---\n{FRONTMATTER.format(h=h, sh=sh)}\n---\n{body}")
    if reviewed:
        record.with_suffix(".review.json").write_text(json.dumps({"reviews": []}))
    source = tmp_path / "page.html"
    source.write_bytes(source_bytes)
    return store, record, source


def test_refresh_keeps_identity_and_frontmatter_but_replaces_body(tmp_path):
    store, record, source = _store(tmp_path)
    outcome = refresh_record(record, store, FRESH_BODY, source, media_type="web")
    assert outcome.written, outcome.reason
    text = record.read_text()
    assert record.exists() and list(store.glob("*.md")) == [record]
    assert "content_hash: sha256:" + "a" * 64 in text
    assert "date_accessed: 2026-08-13T00:48:13+00:00" in text
    assert 'publisher: "Liberation Times"' in text
    assert "**" not in text
    assert "“grave concerns” that the death appears “suspicious”" in text
    assert "date_extracted: 2026-08-13" not in text
    assert "  pipeline_version: 7" in text
    assert "  version: d4f18fe" not in text


def test_refresh_carries_stored_media_file_into_fresh_annotation(tmp_path):
    store, record, source = _store(tmp_path)
    refresh_record(record, store, FRESH_BODY, source, media_type="web")
    assert "  file: 5f2f548aea81.jpg" in record.read_text()


def test_refresh_is_a_no_op_when_nothing_changes(tmp_path):
    store, record, source = _store(tmp_path)
    refresh_record(record, store, FRESH_BODY, source, media_type="web")
    before = record.read_text()
    outcome = refresh_record(record, store, FRESH_BODY, source, media_type="web")
    assert not outcome.written and outcome.reason == "unchanged"
    assert record.read_text() == before


def test_refresh_refuses_when_prose_goes_missing(tmp_path):
    store, record, source = _store(tmp_path)
    before = record.read_text()
    outcome = refresh_record(
        record, store, "Written by Christopher Sharp.\n", source, media_type="web"
    )
    assert not outcome.written
    assert outcome.reason.startswith("refused")
    assert "suggesting" in outcome.reason
    after = record.read_text()
    fm_after, body_after = after[4:].split("\n---\n", 1)
    fm_before, body_before = before[4:].split("\n---\n", 1)
    assert body_after == body_before
    assert "refresh_refused:" in fm_after
    assert re.sub(r"refresh_refused:\n(  .*\n?)*", "", fm_after).rstrip() == fm_before


def test_reviewed_record_ports_markers_and_is_flagged_for_verification(tmp_path):
    store, record, source = _store(tmp_path, reviewed=True)
    fresh = FRESH_BODY + "\nLove our content and wish to support the website?\n"
    outcome = refresh_record(record, store, fresh, source, media_type="web")
    assert outcome.written, outcome.reason
    text = record.read_text()
    assert "<!-- irrelevant: start -->\n\nLove our content" in text
    assert "review_carryover:\n  at: " in text
    assert "  from: " + "a" * 64 in text
    assert "  had_text_edits: true" in text


def test_reviewed_record_tolerates_no_loss_outside_irrelevant_regions(tmp_path):
    store, record, source = _store(tmp_path, reviewed=True)
    # The footer inside the reviewer's irrelevant region may vanish; a word of
    # the reviewed prose may not.
    outcome = refresh_record(record, store, FRESH_BODY, source, media_type="web")
    assert outcome.written, outcome.reason
    store, record, source = _store(tmp_path / "second", reviewed=True)
    trimmed = FRESH_BODY.replace(" may have been silenced", "")
    outcome = refresh_record(record, store, trimmed, source, media_type="web")
    assert not outcome.written and "silenced" in outcome.reason


def test_words_gone_ignores_reordering_captions_and_irrelevant_regions():
    gone = words_gone(OLD_BODY, FRESH_BODY)
    assert gone == {}


def test_transplant_pairs_by_alt_caption_when_counts_differ():
    old = "<!--\nimage:\n  file: one.jpg\n  alt: first\n-->\n\n<!--\nimage:\n  file: two.jpg\n  alt: second\n-->\n"
    new = "<!--\nimage:\n  alt: second\n-->\n\n<!--\nimage:\n  alt: avatar\n-->\n"
    body, note = transplant_image_files(old, new)
    assert "  file: two.jpg\n  alt: second" in body
    assert "avatar" not in body
    assert body.rstrip().endswith("  file: one.jpg\n  alt: first\n-->")
    assert "1 paired, 1 fresh dropped, 1 stored appended" in note


def test_transplant_keeps_a_fresh_download_when_nothing_pairs_with_it():
    old = "<!--\nimage:\n  file: old.jpg\n  alt: first\n-->\n"
    new = "<!--\nimage:\n  file: new.jpg\n-->\n"
    body, _ = transplant_image_files(old, new)
    assert body.startswith("<!--\nimage:\n  file: new.jpg\n-->")
    assert body.rstrip().endswith("  file: old.jpg\n  alt: first\n-->")


def test_port_markers_wraps_the_merged_paragraph():
    old = "<!-- irrelevant: start -->\n\nAdvertise with us today\n\nand reach readers\n\n<!-- irrelevant: end -->\n\nReal prose here.\n"
    new = "Advertise with us today and reach readers\n\nReal prose here.\n"
    body, ported, unported = port_irrelevant_markers(old, new)
    assert ported == 1 and unported == 0
    assert body.startswith(
        "<!-- irrelevant: start -->\n\nAdvertise with us today and reach readers\n\n<!-- irrelevant: end -->"
    )
    assert "Real prose here." in body


def test_restamp_inserts_pipeline_version_when_absent():
    fm = 'processing:\n  handler: webpage\n  version: abc\n  tools:\n    - name: trafilatura\n      version: "2.1.0"'
    out = restamp(fm, "a" * 64, None, media_type="web", tool_version="2.2.0")
    assert "processing:\n  pipeline_version: 7\n  handler: webpage" in out
    assert 'version: "2.2.0"' in out
    assert "review_carryover" not in out


def test_transplant_keeps_the_stored_file_for_the_same_picture():
    old = '<!--\nimage:\n  file: stored.jpg\n  caption: "Photo"\n-->\n'
    new = '<!--\nimage:\n  file: fresh.webp\n  caption: "Photo"\n-->\n'
    body, _ = transplant_image_files(old, new)
    assert "file: stored.jpg" in body and "fresh.webp" not in body


def test_inline_highlight_is_re_placed_around_the_same_prose():
    from refresh import port_inline_markers

    old = (
        "Burlison has said he has\n\n"
        "“grave concerns”, suggesting the {{highlight-start: h7}}officer may have been "
        "silenced{{highlight-end: h7}} before he could speak.\n"
    )
    new = (
        "Burlison has said he has “grave concerns” that the death appears “suspicious”, "
        "suggesting the officer may have been silenced before he could speak.\n"
    )
    body, placed, dropped = port_inline_markers(old, new)
    assert (placed, dropped) == (2, 0)
    assert (
        "suggesting the {{highlight-start: h7}}officer may have been silenced"
        "{{highlight-end: h7}} before he could speak." in body
    )


def test_inline_pair_without_a_home_is_dropped_whole():
    from refresh import port_inline_markers

    old = 'Kept prose stays here. {{note-start: [n1, "a note"]}}Vanished prose{{note-end: n1}} was here.\n'
    new = "Kept prose stays here. was here.\n"
    body, placed, dropped = port_inline_markers(old, new)
    assert dropped == 1 and "{{" not in body


def test_reviewed_record_refuses_when_a_highlight_has_no_home(tmp_path):
    body = OLD_BODY.replace(
        "Written by Christopher Sharp - 24 April 2026",
        "Written by {{highlight-start: a1}}Christopher Sharp{{highlight-end: a1}} - 24 April 2026",
    )
    store, record, source = _store(tmp_path, body=body, reviewed=True)
    outcome = refresh_record(record, store, FRESH_BODY, source, media_type="web")
    assert outcome.written, outcome.reason
    assert (
        "{{highlight-start: a1}}Christopher Sharp{{highlight-end: a1}}"
        in record.read_text()
    )
    store, record, source = _store(tmp_path / "second", body=body, reviewed=True)
    fresh = FRESH_BODY.replace(
        "Written by [Christopher Sharp](https://twitter.com/x) - 24 April 2026",
        "Written by the desk - 24 April 2026",
    )
    outcome = refresh_record(record, store, fresh, source, media_type="web")
    assert (
        not outcome.written
        and "marker pair" in outcome.reason
        or "absent" in outcome.reason
    )


def test_a_jammed_token_is_not_a_lost_word():
    gone = words_gone(
        "The report came withexecutive approval.\n",
        "The report came with executive approval.\n",
    )
    assert gone == {}
    gone = words_gone(
        "The report came with executive approval.\n", "The report came with approval.\n"
    )
    assert gone == {"executive": 1}


def test_a_refusal_is_stamped_on_the_record_and_a_later_success_clears_it(tmp_path):
    store, record, source = _store(tmp_path, reviewed=True)
    trimmed = FRESH_BODY.replace(" may have been silenced", "")
    outcome = refresh_record(record, store, trimmed, source, media_type="web")
    assert not outcome.written
    text = record.read_text()
    assert "refresh_refused:\n  at: " in text
    assert '  reason: "refused: ' in text and "silenced" in text
    assert text.endswith(OLD_BODY)  # body untouched
    outcome = refresh_record(record, store, FRESH_BODY, source, media_type="web")
    assert outcome.written, outcome.reason
    assert "refresh_refused" not in record.read_text()


def test_a_dateline_or_byline_the_frontmatter_carries_is_not_lost(tmp_path):
    from refresh import carried_words

    fm = FRONTMATTER.format(h="a" * 64, sh="b" * 64) + "\ncreators:\n- Keith Kloor\n"
    carried = carried_words(fm)
    old = "Keith Kloor\n\nApril 24, 2026\n\nThe article prose that stays.\n"
    new = "The article prose that stays.\n"
    assert words_gone(old, new, carried) == {}
    assert words_gone(old, new) != {}


def test_carry_review_work_returns_the_carried_body_or_a_refusal():
    carry = carry_review_work(OLD_BODY, FRESH_BODY, "", reviewed=True)
    assert carry.refused is None and carry.prose_moved
    assert "  file: 5f2f548aea81.jpg" in carry.body and "**" not in carry.body
    carry = carry_review_work(
        OLD_BODY, "Written by Christopher Sharp.\n", "", reviewed=True
    )
    assert carry.refused and carry.refused.startswith("refused")


def test_a_broken_image_comment_is_not_counted_as_prose():
    old = (
        "Prose that stays here.\n\n<!--\nimage:\n  file: fe7450b10949.gif\n"
        '  alt: "Video player loading"\n<!-- irrelevant: start -->\n\nFooter\n\n<!-- irrelevant: end -->\n'
    )
    new = 'Prose that stays here.\n\n<!--\nimage:\n  file: abc.gif\n  alt: "Video player loading"\n-->\n'
    assert words_gone(old, new) == {}


def test_a_field_the_stored_record_already_lacked_does_not_refuse_the_refresh(tmp_path):
    store, record, source = _store(tmp_path)
    text = record.read_text().replace("date_published: 2026-04-24\n", "")
    record.write_text(text)
    outcome = refresh_record(record, store, FRESH_BODY, source, media_type="web")
    assert outcome.written, outcome.reason
    assert any("pre-existing" in n for n in outcome.notes)
    assert "date_published" not in record.read_text()


def test_a_refresh_that_moves_no_content_line_leaves_the_review_standing(tmp_path):
    store, record, source = _store(tmp_path, reviewed=True)
    refresh_record(record, store, FRESH_BODY, source, media_type="web")
    text = record.read_text()
    assert "review_carryover:" in text
    record.write_text(text.replace("review_carryover:", "review_carryover_old:"))
    # Only a link target changes: every content line keeps its text and place.
    retouched = FRESH_BODY.replace("https://unsplash.com/x", "https://unsplash.com/y")
    outcome = refresh_record(record, store, retouched, source, media_type="web")
    assert outcome.written, outcome.reason
    assert "review_carryover:\n" not in record.read_text()
    assert any("left standing" in n for n in outcome.notes)


def test_renumbered_footnotes_and_roman_page_numbers_are_not_lost_words():
    old = "As Vallee2 argued, iii\n\nand Lucas3 agreed. xiv\n"
    new = "As Vallee[^37] argued,\n\nand Lucas[^38] agreed.\n"
    assert words_gone(old, new) == {}
    assert words_gone("As Vallee2 argued.\n", "As argued.\n") == {"vallee2": 1}


def test_uncaptioned_stored_annotations_with_no_fresh_counterpart_are_dropped():
    old = "Prose.\n\n<!--\nimage:\n  file: thumb1.jpg\n-->\n\n<!--\nimage:\n  file: thumb2.jpg\n-->\n"
    body, note = transplant_image_files(old, "Prose.\n")
    assert "thumb" not in body and "2 uncaptioned dropped" in note
    old2 = (
        'Prose.\n\n<!--\nimage:\n  file: lead.jpg\n  caption: "Above: the plant"\n-->\n'
    )
    body, _ = transplant_image_files(old2, "Prose.\n")
    assert "lead.jpg" in body
