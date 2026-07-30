import quality


def _record(source_type: str, body: str, extra_frontmatter: str = "") -> str:
    fm = f"schema: anomalica/record/1\nsource_type: {source_type}"
    if extra_frontmatter:
        fm += "\n" + extra_frontmatter
    return f"---\n{fm}\n---\n{body}"


def test_replacement_chars_counted():
    m = quality.measure("clean text with a � and another �", "web")
    assert m["replacement_chars"] == 2


def test_substitution_score_flags_ocr_variant():
    # "Valensole" spelled correctly several times and once as the OCR variant.
    body = " ".join(["Valensole"] * 4) + " Valcnsolc " + " ".join(["Warminster"] * 3)
    score, flagged = quality.substitution_score(body)
    assert score > 0
    assert any("Valcnsolc->Valensole" == f for f in flagged)


def test_substitution_score_ignores_distinct_real_names():
    # Harold and Herald are both real words - a resemblance, not a corruption.
    body = " ".join(["Harold"] * 3 + ["Herald"] * 3)
    score, flagged = quality.substitution_score(body)
    assert flagged == []
    assert score == 0.0


def test_measure_omits_structure_fields_for_audio():
    m = quality.measure("a transcript body", "audio")
    assert "chapter_markers" not in m
    assert "page_anchors" not in m
    assert "replacement_chars" in m


def test_measure_page_anchors_zero_is_present_for_pdf():
    # A PDF with no printed_page markers: measured-none, must be present as 0.
    m = quality.measure("<!-- file_page: 1 -->\nbody", "pdf")
    assert m["page_anchors"] == 0


def test_stamp_injects_block_and_preserves_body_and_frontmatter():
    record = _record(
        "ebook",
        '<!-- chapter: 1 -->\n<!-- chapter_title: "One" -->\nbody text',
        extra_frontmatter='title: "A Book"\ncreators:\n  - Someone\ncontent_hash: sha256:abc',
    )
    stamped = quality.stamp_record(record)
    assert "quality:" in stamped
    assert "chapter_markers: 1" in stamped
    assert "chapter_titles: 1" in stamped
    # body and the other frontmatter fields survive untouched
    assert stamped.split("\n---\n", 1)[1] == record.split("\n---\n", 1)[1]
    assert "content_hash: sha256:abc" in stamped
    assert "- Someone" in stamped


def test_stamp_is_idempotent():
    record = _record("web", "body")
    once = quality.stamp_record(record)
    twice = quality.stamp_record(once)
    assert once == twice
    assert once.count("quality:") == 1


def test_stamp_replaces_stale_block():
    record = _record(
        "web", "body", extra_frontmatter="quality:\n  replacement_chars: 999"
    )
    stamped = quality.stamp_record(record)
    assert "replacement_chars: 999" not in stamped
    assert stamped.count("quality:") == 1


def test_stamp_noop_without_source_type():
    record = "---\nschema: anomalica/record/1\n---\nbody"
    assert quality.stamp_record(record) == record
