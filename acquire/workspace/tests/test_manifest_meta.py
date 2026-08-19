from manifest_meta import video_metadata_fields

# A realistic yt-dlp --dump-json info dict for a YouTube video (the fields the
# pipeline actually reads), modelled on the DEBRIEFED episodes that regressed.
SAMPLE_INFO = {
    "title": "UFO Witness Reveals CHILLING Daytime Alien Encounter - Peter Khoury - DEBRIEFED ep. 58",
    "upload_date": "20251017",
    "duration": 8016,
    "channel": "Area52",
    "description": "A first-hand account...",
    "extractor": "youtube",
    "id": "wX3whEVHr3g",
}


def test_maps_the_canonical_record_fields():
    """The fields a reprocess must carry so its record matches a fresh URL ingest -
    title, posted_by, posted_date, duration, source_id. This is the regression: a
    record that came out titled with the raw URL, no channel, dated the reprocess
    day."""
    fields = video_metadata_fields(SAMPLE_INFO)
    assert fields["title"].startswith("UFO Witness Reveals CHILLING")
    assert fields["posted_by"] == "Area52"
    assert fields["posted_date"] == "2025-10-17"  # YYYYMMDD -> ISO
    assert fields["duration"] == 8016
    assert fields["source_id"] == "youtube:wX3whEVHr3g"
    # the title must never be a bare URL
    assert not fields["title"].startswith("http")


def test_omits_missing_fields_rather_than_writing_null():
    """Keys are omitted when absent so manifest.update() can't clobber good values
    with empties - the caller stamps source_url/source_id itself."""
    fields = video_metadata_fields({"title": "Only a title"})
    assert fields == {"title": "Only a title"}
    assert "posted_by" not in fields and "posted_date" not in fields


def test_rejects_malformed_upload_date_and_zero_duration():
    fields = video_metadata_fields(
        {"title": "T", "upload_date": "2025", "duration": 0, "channel": ""}
    )
    assert "date" not in fields  # not 8 digits
    assert "duration" not in fields  # zero is not a real length
    assert "publisher" not in fields  # empty channel dropped


def test_empty_or_nondict_input_is_safe():
    assert video_metadata_fields({}) == {}
    assert video_metadata_fields(None) == {}


def test_source_id_needs_both_extractor_and_id():
    assert "source_id" not in video_metadata_fields({"id": "abc"})
    assert "source_id" not in video_metadata_fields({"extractor": "youtube"})
    assert (
        video_metadata_fields({"extractor": "vimeo", "id": "123"})["source_id"]
        == "vimeo:123"
    )


def test_never_claims_the_channel_is_the_publisher():
    """A YouTube channel is the source of the COPY. It is the publisher of the work
    only when it also produced it, and yt-dlp cannot tell the difference - so the
    mapper must not assert either. All 178 video records in the corpus carry a
    channel as `publisher` because it did."""
    fields = video_metadata_fields(SAMPLE_INFO)
    assert "publisher" not in fields
    assert "date_published" not in fields
