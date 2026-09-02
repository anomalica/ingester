import json

import pytest

import ingest_pdf


def _record(path, body, reviewed=False):
    path.write_text(
        "---\nschema: anomalica/record/1\ntitle: Report\ndate_published: 2020\n"
        "source_type: pdf\ncontent_hash: sha256:" + "a" * 64 + "\n---\n" + body
    )
    if reviewed:
        path.with_suffix(".review.json").write_text(json.dumps({"reviews": []}))
    return path


def test_force_reextraction_carries_a_reviewers_regions_and_flags_the_record(tmp_path):
    old_body = "<!-- irrelevant: start -->\n\nRunning header text of the report.\n\n<!-- irrelevant: end -->\n\nThe craft was recovered intact, the witness said.\n"
    existing = _record(tmp_path / ("a" * 64 + ".md"), old_body, reviewed=True)
    fresh = (
        "---\nschema: anomalica/record/1\ntitle: Report\ndate_published: 2020\nsource_type: pdf\ncontent_hash: sha256:"
        + "a" * 64
        + "\n---\nRunning header text of the report.\n\nThe craft was recovered intact, the witness said.\n"
    )
    out = ingest_pdf._carry_review_work(existing, fresh)
    assert "<!-- irrelevant: start -->\n\nRunning header text of the report." in out
    assert "review_carryover:\n  at: " in out and "  had_text_edits: false" in out


def test_force_reextraction_that_loses_reviewed_prose_refuses_and_stamps(tmp_path):
    existing = _record(
        tmp_path / ("a" * 64 + ".md"),
        "The craft was recovered intact, the witness said.\n",
        reviewed=True,
    )
    fresh = (
        "---\nschema: anomalica/record/1\ntitle: Report\ndate_published: 2020\nsource_type: pdf\ncontent_hash: sha256:"
        + "a" * 64
        + "\n---\nThe craft was recovered, the witness said.\n"
    )
    with pytest.raises(SystemExit):
        ingest_pdf._carry_review_work(existing, fresh)
    text = existing.read_text()
    assert "refresh_refused:" in text and "intact" in text
    assert text.endswith("The craft was recovered intact, the witness said.\n")
