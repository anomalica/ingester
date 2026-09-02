import hashlib
import json
from unittest.mock import patch

from extraction.trafilatura_ext import Article

import ingest_webpage
from refresh import (
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
    outcome = refresh_record(record, store, FRESH_BODY, source)
    assert outcome.written, outcome.reason
    text = record.read_text()
    assert record.exists() and list(store.glob("*.md")) == [record]
    assert "content_hash: sha256:" + "a" * 64 in text
    assert "date_accessed: 2026-08-13T00:48:13+00:00" in text
    assert 'publisher: "Liberation Times"' in text
    assert "**" not in text
    assert "“grave concerns” that the death appears “suspicious”" in text
    assert "date_extracted: 2026-08-13" not in text
    assert "  pipeline_version: 3" in text
    assert "  version: d4f18fe" not in text


def test_refresh_carries_stored_media_file_into_fresh_annotation(tmp_path):
    store, record, source = _store(tmp_path)
    refresh_record(record, store, FRESH_BODY, source)
    assert "  file: 5f2f548aea81.jpg" in record.read_text()


def test_refresh_is_a_no_op_when_nothing_changes(tmp_path):
    store, record, source = _store(tmp_path)
    refresh_record(record, store, FRESH_BODY, source)
    before = record.read_text()
    outcome = refresh_record(record, store, FRESH_BODY, source)
    assert not outcome.written and outcome.reason == "unchanged"
    assert record.read_text() == before


def test_refresh_refuses_when_prose_goes_missing(tmp_path):
    store, record, source = _store(tmp_path)
    before = record.read_text()
    outcome = refresh_record(record, store, "Written by Christopher Sharp.\n", source)
    assert not outcome.written
    assert outcome.reason.startswith("refused")
    assert "suggesting" in outcome.reason
    assert record.read_text() == before


def test_reviewed_record_ports_markers_and_is_flagged_for_verification(tmp_path):
    store, record, source = _store(tmp_path, reviewed=True)
    fresh = FRESH_BODY + "\nLove our content and wish to support the website?\n"
    outcome = refresh_record(record, store, fresh, source)
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
    outcome = refresh_record(record, store, FRESH_BODY, source)
    assert outcome.written, outcome.reason
    store, record, source = _store(tmp_path / "second", reviewed=True)
    trimmed = FRESH_BODY.replace(" - 24 April 2026", "")
    outcome = refresh_record(record, store, trimmed, source)
    assert not outcome.written and "april" in outcome.reason


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
    out = restamp(fm, "a" * 64, review_carryover=None)
    assert "processing:\n  pipeline_version: 3\n  handler: webpage" in out
    assert "review_carryover" not in out


@patch("ingest_webpage.extract_article")
def test_run_refreshes_in_place_when_the_page_bytes_are_already_ingested(
    mock_extract, tmp_path
):
    page = b"<html><body><p>Article</p></body></html>"
    store, record, _ = _store(tmp_path, source_bytes=page)
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "asset.html").write_bytes(page)
    (staging / "manifest.json").write_text(
        json.dumps(
            {
                "source": str(tmp_path / "archived.html"),
                "asset": "asset.html",
                "detected_type": "text/html",
                "fetch_method": "local",
                "fetched_at": "2026-09-02T10:00:00Z",
                "source_url": "https://www.liberationtimes.com/home/late-officer",
            }
        )
    )
    mock_extract.return_value = Article(
        text=FRESH_BODY,
        title="Late Officer",
        authors=None,
        date="2026-04-24",
        sitename="Liberation Times",
        description=None,
    )
    assert ingest_webpage.run(staging, tmp_path / "output", force=False) == 0
    assert "**Burlison" in record.read_text()  # untouched without --force
    assert ingest_webpage.run(staging, tmp_path / "output", force=True) == 0
    assert list(store.glob("*.md")) == [record]
    assert "“grave concerns” that the death appears" in record.read_text()


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
    outcome = refresh_record(record, store, FRESH_BODY, source)
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
    outcome = refresh_record(record, store, fresh, source)
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
