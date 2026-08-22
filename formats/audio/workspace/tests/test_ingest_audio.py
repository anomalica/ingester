import json
import re
from unittest.mock import patch

from models import Segment, SpeakerSegment, Word

MOCK_SEGMENTS = [
    Segment(
        text="Hello there",
        start=0.0,
        end=2.0,
        words=[
            Word(text="Hello", start=0.0, end=0.5),
            Word(text="there", start=0.6, end=1.0),
        ],
    ),
    Segment(
        text="I am fine thanks",
        start=2.5,
        end=5.0,
        words=[
            Word(text="I", start=2.5, end=2.6),
            Word(text="am", start=2.7, end=2.9),
            Word(text="fine", start=3.0, end=3.3),
            Word(text="thanks", start=3.4, end=3.8),
        ],
    ),
]

MOCK_SPEAKER_SEGMENTS = [
    SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=2.0),
    SpeakerSegment(speaker="SPEAKER_01", start=2.5, end=5.0),
]

# Raw whisperx/pyannote output the (segments, raw)/(speakers, raw) tuples carry.
# Mirrors MOCK_SEGMENTS / MOCK_SPEAKER_SEGMENTS so load_raw_archive reconstructs
# the same processed forms on a cache-reuse run.
MOCK_WHISPERX_RAW = {
    "language": "en",
    "transcribe": {"language": "en", "segments": []},
    "aligned": {
        "segments": [
            {
                "text": "Hello there",
                "start": 0.0,
                "end": 2.0,
                "words": [
                    {"word": "Hello", "start": 0.0, "end": 0.5, "score": 0.9},
                    {"word": "there", "start": 0.6, "end": 1.0, "score": 0.9},
                ],
            },
            {
                "text": "I am fine thanks",
                "start": 2.5,
                "end": 5.0,
                "words": [
                    {"word": "I", "start": 2.5, "end": 2.6},
                    {"word": "am", "start": 2.7, "end": 2.9},
                    {"word": "fine", "start": 3.0, "end": 3.3},
                    {"word": "thanks", "start": 3.4, "end": 3.8},
                ],
            },
        ]
    },
}
MOCK_PYANNOTE_RAW = {
    "model": "test",
    "tracks": [
        {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00", "track": "A"},
        {"start": 2.5, "end": 5.0, "speaker": "SPEAKER_01", "track": "B"},
    ],
}


def _create_staging(
    tmp_path, mime_type="audio/mpeg", source="https://example.com/ep.mp3"
):
    """Create a staging directory with manifest and dummy audio asset."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "asset.mp3").write_bytes(b"fake audio data")
    manifest = {
        "source": source,
        "asset": "asset.mp3",
        "detected_type": mime_type,
        "fetch_method": "http",
        "fetched_at": "2026-03-31T10:00:00Z",
    }
    (staging / "manifest.json").write_text(json.dumps(manifest))
    return staging


@patch("ingest_audio.diarise", return_value=(MOCK_SPEAKER_SEGMENTS, MOCK_PYANNOTE_RAW))
@patch("ingest_audio.transcribe", return_value=(MOCK_SEGMENTS, MOCK_WHISPERX_RAW))
def test_run_writes_record(mock_transcribe, mock_diarise, tmp_path):
    import ingest_audio

    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    result = ingest_audio.run(staging, output, force=False)

    assert result == 0
    md_files = list((output / "store").glob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text()
    assert "schema: anomalica/record/1" in content
    assert "source_type: audio" in content
    assert "<!-- speaker: [speaker 1] -->" in content
    assert "<!-- speaker: [speaker 2] -->" in content
    assert "Hello there" in content
    assert "I am fine thanks" in content


@patch("ingest_audio.diarise", return_value=(MOCK_SPEAKER_SEGMENTS, MOCK_PYANNOTE_RAW))
@patch("ingest_audio.transcribe", return_value=(MOCK_SEGMENTS, MOCK_WHISPERX_RAW))
def test_run_stamps_source_url_from_manifest_for_local_source(
    mock_transcribe, mock_diarise, tmp_path
):
    """Re-processing an archived local source: `source` is a path (no origin),
    but the manifest supplies the known source_url/source_id, which must be
    stamped onto the record rather than dropped."""
    import ingest_audio

    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "asset.opus").write_bytes(b"fake audio data")
    manifest = {
        "source": "/archive/sources/abc.opus",  # local path, not a URL
        "asset": "asset.opus",
        "detected_type": "audio/opus",
        "fetch_method": "local",
        "fetched_at": "2026-07-05T00:00:00Z",
        "source_url": "https://www.youtube.com/watch?v=wX3whEVHr3g",
        "source_id": "youtube:wX3whEVHr3g",
    }
    (staging / "manifest.json").write_text(json.dumps(manifest))
    output = tmp_path / "output"
    ingest_audio.run(staging, output, force=False)

    content = list((output / "store").glob("*.md"))[0].read_text()
    assert "source_url: https://www.youtube.com/watch?v=wX3whEVHr3g" in content
    assert "source_id: youtube:wX3whEVHr3g" in content


@patch("ingest_audio.diarise", return_value=(MOCK_SPEAKER_SEGMENTS, MOCK_PYANNOTE_RAW))
@patch("ingest_audio.transcribe", return_value=(MOCK_SEGMENTS, MOCK_WHISPERX_RAW))
def test_run_includes_processing_in_frontmatter(
    mock_transcribe, mock_diarise, tmp_path
):
    import ingest_audio

    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_audio.run(staging, output, force=False)

    md_files = list((output / "store").glob("*.md"))
    content = md_files[0].read_text()
    assert "processing:" in content
    assert "handler: audio" in content
    assert "name: whisperx" in content
    assert "role: transcription" in content
    assert "name: pyannote" in content
    assert "role: diarisation" in content
    assert "provider: local" in content
    assert "date_extracted:" in content


@patch("ingest_audio.diarise", return_value=(MOCK_SPEAKER_SEGMENTS, MOCK_PYANNOTE_RAW))
@patch("ingest_audio.transcribe", return_value=(MOCK_SEGMENTS, MOCK_WHISPERX_RAW))
def test_run_creates_symlink(mock_transcribe, mock_diarise, tmp_path):
    import ingest_audio

    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_audio.run(staging, output, force=False)

    links = list((output / "by-name").glob("*.md"))
    assert len(links) == 1
    assert links[0].is_symlink()
    assert "audio" in links[0].name


@patch("ingest_audio.diarise", return_value=(MOCK_SPEAKER_SEGMENTS, MOCK_PYANNOTE_RAW))
@patch("ingest_audio.transcribe", return_value=(MOCK_SEGMENTS, MOCK_WHISPERX_RAW))
def test_run_skips_existing(mock_transcribe, mock_diarise, tmp_path):
    import ingest_audio

    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_audio.run(staging, output, force=False)
    ingest_audio.run(staging, output, force=False)

    md_files = list((output / "store").glob("*.md"))
    assert len(md_files) == 1
    assert mock_transcribe.call_count == 1


@patch("ingest_audio.diarise", return_value=(MOCK_SPEAKER_SEGMENTS, MOCK_PYANNOTE_RAW))
@patch("ingest_audio.transcribe", return_value=(MOCK_SEGMENTS, MOCK_WHISPERX_RAW))
def test_run_force_reprocesses(mock_transcribe, mock_diarise, tmp_path):
    """--force re-renders the record but reuses the cached transcription
    (cheap); only --no-cache forces fresh re-transcription."""
    import ingest_audio

    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_audio.run(staging, output, force=False)  # transcribe #1, caches
    ingest_audio.run(staging, output, force=True)  # re-render from cache
    assert mock_transcribe.call_count == 1

    ingest_audio.run(staging, output, force=True, use_cache=False)  # fresh GPU
    assert mock_transcribe.call_count == 2


def test_run_fails_missing_manifest(tmp_path):
    import ingest_audio

    staging = tmp_path / "staging"
    staging.mkdir()
    output = tmp_path / "output"

    assert ingest_audio.run(staging, output, force=False) != 0


def test_run_fails_missing_asset(tmp_path):
    import ingest_audio

    staging = tmp_path / "staging"
    staging.mkdir()
    manifest = {
        "source": "https://example.com",
        "asset": "asset.mp3",
        "detected_type": "audio/mpeg",
    }
    (staging / "manifest.json").write_text(json.dumps(manifest))
    output = tmp_path / "output"

    assert ingest_audio.run(staging, output, force=False) != 0


@patch("ingest_audio.diarise", return_value=(MOCK_SPEAKER_SEGMENTS, MOCK_PYANNOTE_RAW))
@patch("ingest_audio.transcribe", return_value=(MOCK_SEGMENTS, MOCK_WHISPERX_RAW))
def test_run_video_source_type(mock_transcribe, mock_diarise, tmp_path):
    import ingest_audio

    staging = _create_staging(
        tmp_path, mime_type="video/mp4", source="https://example.com/vid.mp4"
    )
    (staging / "asset.mp4").write_bytes(b"fake video data")
    manifest = json.loads((staging / "manifest.json").read_text())
    manifest["asset"] = "asset.mp4"
    (staging / "manifest.json").write_text(json.dumps(manifest))

    output = tmp_path / "output"
    ingest_audio.run(staging, output, force=False)

    md_files = list((output / "store").glob("*.md"))
    content = md_files[0].read_text()
    assert "source_type: video" in content


@patch("ingest_audio.diarise", return_value=(MOCK_SPEAKER_SEGMENTS, MOCK_PYANNOTE_RAW))
@patch("ingest_audio.transcribe", return_value=(MOCK_SEGMENTS, MOCK_WHISPERX_RAW))
def test_run_speakers_in_body_not_frontmatter(mock_transcribe, mock_diarise, tmp_path):
    import ingest_audio

    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_audio.run(staging, output, force=False)

    md_files = list((output / "store").glob("*.md"))
    content = md_files[0].read_text()
    # Speakers should be in body annotations, not frontmatter
    frontmatter = content.split("---", 2)[1]
    assert "speakers:" not in frontmatter
    # But speaker turns should be in the body
    assert "<!-- speaker: [speaker 1] -->" in content
    assert "<!-- speaker: [speaker 2] -->" in content


@patch("ingest_audio.diarise", return_value=(MOCK_SPEAKER_SEGMENTS, MOCK_PYANNOTE_RAW))
@patch("ingest_audio.transcribe", return_value=(MOCK_SEGMENTS, MOCK_WHISPERX_RAW))
def test_run_duration_in_frontmatter(mock_transcribe, mock_diarise, tmp_path):
    import ingest_audio

    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_audio.run(staging, output, force=False)

    md_files = list((output / "store").glob("*.md"))
    content = md_files[0].read_text()
    assert "duration:" in content


@patch("ingest_audio.probe")
@patch("ingest_audio.diarise", return_value=(MOCK_SPEAKER_SEGMENTS, MOCK_PYANNOTE_RAW))
@patch("ingest_audio.transcribe", return_value=(MOCK_SEGMENTS, MOCK_WHISPERX_RAW))
def test_run_duration_is_the_file_not_the_transcript(
    mock_transcribe, mock_diarise, mock_probe, tmp_path
):
    """duration is the FILE's length, not the transcript's span.

    A file ending in silence, outro music or applause transcribes to a last
    segment well short of its end. Declaring that as the duration understated
    every audio record in the corpus (worst: 82s missing), so a probed duration
    must win over the transcript even though both are plausible numbers.
    """
    import ingest_audio

    mock_probe.return_value = {
        "codec": "mp3",
        "container": "mp3",
        "sample_rate": 44100,
        "bitrate": 128000,
        "size_bytes": 15,
        "channels": 1,
        "duration": 128.44,  # file runs on well past the last word
    }
    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_audio.run(staging, output, force=False)

    content = list((output / "store").glob("*.md"))[0].read_text()
    assert "duration: 128.44" in content
    assert f"duration: {MOCK_SEGMENTS[-1].end}" not in content


@patch("ingest_audio.probe")
@patch("ingest_audio.diarise", return_value=(MOCK_SPEAKER_SEGMENTS, MOCK_PYANNOTE_RAW))
@patch("ingest_audio.transcribe", return_value=(MOCK_SEGMENTS, MOCK_WHISPERX_RAW))
def test_run_duration_falls_back_when_unprobeable(
    mock_transcribe, mock_diarise, mock_probe, tmp_path
):
    """An unprobeable file still gets a duration: the transcript's end is a poor
    floor, but a record with no duration at all is worse."""
    import ingest_audio

    mock_probe.return_value = {
        "codec": None,
        "container": None,
        "sample_rate": None,
        "bitrate": None,
        "size_bytes": 15,
        "channels": None,
        "duration": None,
    }
    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_audio.run(staging, output, force=False)

    content = list((output / "store").glob("*.md"))[0].read_text()
    assert f"duration: {round(MOCK_SEGMENTS[-1].end, 2)}" in content


@patch("ingest_audio.diarise", return_value=(MOCK_SPEAKER_SEGMENTS, MOCK_PYANNOTE_RAW))
@patch("ingest_audio.transcribe", return_value=(MOCK_SEGMENTS, MOCK_WHISPERX_RAW))
def test_run_time_annotations_formatted(mock_transcribe, mock_diarise, tmp_path):
    import ingest_audio

    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_audio.run(staging, output, force=False)

    md_files = list((output / "store").glob("*.md"))
    content = md_files[0].read_text()
    assert "00:00:00.0 Hello there" in content
    assert "00:00:02.5 I am fine thanks" in content


@patch("ingest_audio.diarise", return_value=(MOCK_SPEAKER_SEGMENTS, MOCK_PYANNOTE_RAW))
@patch("ingest_audio.transcribe", return_value=([], {}))
def test_run_fails_on_empty_transcription(mock_transcribe, mock_diarise, tmp_path):
    import ingest_audio

    staging = _create_staging(tmp_path)
    output = tmp_path / "output"

    assert ingest_audio.run(staging, output, force=False) != 0


def test_build_content_emits_word_markers():
    import ingest_audio
    from models import TimedSentence, Turn, Word

    turn = Turn(
        speaker="[speaker 1]",
        sentences=[
            TimedSentence(
                time=1.2,
                text="Hi there.",
                words=[Word("Hi", 1.2, 1.4), Word("there.", 1.5, 1.9)],
            )
        ],
    )
    body = ingest_audio._build_content([turn], word_timestamps=True)
    assert "{{t:1.20}}Hi {{t:1.50}}there." in body
    # word-level lines drop the redundant HH:MM:SS.D line-start prefix - the
    # first {{t:}} marker is the line start.
    assert not re.search(r"(?m)^\d{2}:\d{2}:\d{2}\.\d \{\{t:", body)
    # default (record/1) path stays plain prose with a sentence line-start prefix
    default_body = ingest_audio._build_content([turn])
    assert "{{t:" not in default_body
    assert re.search(r"(?m)^\d{2}:\d{2}:\d{2}\.\d Hi there\.$", default_body)


def test_build_content_wordless_segment_keeps_prefix():
    import ingest_audio
    from models import TimedSentence, Turn

    # A segment the aligner left without word-level timing (words=None) keeps
    # its HH:MM:SS.D line-start stamp even in word_timestamps mode - it is the
    # line's only timing, so stripping it would lose it.
    turn = Turn(
        speaker="[speaker 1]",
        sentences=[TimedSentence(time=5735.6, text="Thank you.", words=None)],
    )
    body = ingest_audio._build_content([turn], word_timestamps=True)
    assert "01:35:35.6 Thank you." in body
    assert "{{t:" not in body


@patch("ingest_audio.diarise", return_value=(MOCK_SPEAKER_SEGMENTS, MOCK_PYANNOTE_RAW))
@patch("ingest_audio.transcribe", return_value=(MOCK_SEGMENTS, MOCK_WHISPERX_RAW))
def test_raw_archive_written_then_reused(mock_transcribe, mock_diarise, tmp_path):
    """First run transcribes and writes the raw archive to records/; a forced
    re-run reuses the archive and does NOT call the GPU again."""
    import ingest_audio

    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    records = tmp_path / "records"  # output.parent / "records"

    ingest_audio.run(staging, output, force=False)  # fresh: transcribes + archives
    archives = list(records.glob("*.transcript.json"))
    assert len(archives) == 1
    assert mock_transcribe.call_count == 1
    assert mock_diarise.call_count == 1

    # force past the record-exists skip; archive should serve, GPU untouched
    ingest_audio.run(staging, output, force=True)
    assert mock_transcribe.call_count == 1  # not called again
    assert mock_diarise.call_count == 1


@patch("ingest_audio.diarise", return_value=(MOCK_SPEAKER_SEGMENTS, MOCK_PYANNOTE_RAW))
@patch("ingest_audio.transcribe", return_value=(MOCK_SEGMENTS, MOCK_WHISPERX_RAW))
def test_no_cache_skips_archive(mock_transcribe, mock_diarise, tmp_path):
    import ingest_audio

    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_audio.run(staging, output, force=False, use_cache=False)
    assert list((tmp_path / "records").glob("*.transcript.json")) == []


@patch("ingest_audio.diarise", return_value=(MOCK_SPEAKER_SEGMENTS, MOCK_PYANNOTE_RAW))
@patch("ingest_audio.transcribe", return_value=(MOCK_SEGMENTS, MOCK_WHISPERX_RAW))
def test_run_word_mode_writes_v2_with_markers(mock_transcribe, mock_diarise, tmp_path):
    import ingest_audio

    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    result = ingest_audio.run(staging, output, force=False, word_timestamps=True)

    assert result == 0
    all_md = list((output / "store").glob("*.md"))
    assert len(all_md) == 1 and all_md[0].name.endswith(".v2.md")
    content = all_md[0].read_text()
    assert "schema: anomalica/record/2" in content
    assert "word_timestamps: true" in content
    # title stays the proper title (no capability prefix) - v2 is signalled by
    # schema/word_timestamps, and v1 is moved to v1/ so they don't co-list
    assert "{{t:0.00}}Hello" in content


@patch("ingest_audio.diarise", return_value=(MOCK_SPEAKER_SEGMENTS, MOCK_PYANNOTE_RAW))
@patch("ingest_audio.transcribe", return_value=(MOCK_SEGMENTS, MOCK_WHISPERX_RAW))
def test_word_mode_does_not_overwrite_v1(mock_transcribe, mock_diarise, tmp_path):
    import ingest_audio

    staging = _create_staging(tmp_path)
    output = tmp_path / "output"

    ingest_audio.run(staging, output, force=False)  # v1
    v1 = [p for p in (output / "store").glob("*.md") if not p.name.endswith(".v2.md")]
    assert len(v1) == 1
    v1_before = v1[0].read_text()

    ingest_audio.run(staging, output, force=False, word_timestamps=True)  # v2 beside

    assert v1[0].read_text() == v1_before  # v1 untouched
    assert (v1[0].parent / f"{v1[0].stem}.v2.md").exists()
    assert "schema: anomalica/record/1" in v1_before


def test_resolve_title_prefers_manifest_title():
    import ingest_audio

    title = ingest_audio._resolve_title(
        {"title": "Real Title"}, "https://youtu.be/x", "youtube:x", "audio"
    )
    assert title == "Real Title"


def test_resolve_title_never_uses_asset_name():
    import ingest_audio

    # yt-dlp metadata lost (no manifest title): fall back to the source URL,
    # never the asset filename (which is always the literal "asset").
    title = ingest_audio._resolve_title({}, "https://youtu.be/x", "youtube:x", "audio")
    assert title == "https://youtu.be/x"
    assert title != "asset"


def test_resolve_title_falls_back_to_source_id_then_generic():
    import ingest_audio

    assert ingest_audio._resolve_title({}, None, "youtube:x", "audio") == "youtube:x"
    assert ingest_audio._resolve_title({}, None, None, "video") == "Untitled video"


def test_featured_credit_beats_the_pre_colon_split():
    """The pre-colon segment of a clickbait title is a job title, not a person, and
    the real name sits in the credit the split threw away. This put "Presidential
    Advisor", "NASA Chief" and "CIA Contractor" into speaker rosters as people."""
    from ingest_audio import _extract_known_speakers

    names = _extract_known_speakers(
        'Presidential Advisor: "I Directly Handled UFO Material" (Ft. Harald Malmgren)',
        None,
        None,
    )
    assert names == ["Harald Malmgren"]


def test_a_credit_can_name_more_than_one_guest():
    from ingest_audio import _extract_known_speakers

    names = _extract_known_speakers(
        "I Saw A 300ft UFO In The Indonesian Jungle (Ft. Michael Herrera & UAPGerb)",
        None,
        None,
    )
    # UAPGerb is a handle, not a two-or-three-word personal name, so it stays out.
    assert names == ["Michael Herrera"]


def test_the_title_split_still_works_without_a_credit():
    from ingest_audio import _extract_known_speakers

    names = _extract_known_speakers("Jesse Michels: Inside the Program", None, None)
    assert names == ["Jesse Michels"]


def test_a_channel_name_is_not_a_speaker():
    """A channel that reads like a person is still a channel. These reached the
    corpus as people: Lehto Files, Bigelow Podcast, NIGHT SHIFT."""
    from ingest_audio import _extract_known_speakers

    for channel in ("Lehto Files", "Bigelow Podcast", "NIGHT SHIFT", "AetherNet TV"):
        assert _extract_known_speakers("An Episode", None, channel) == [], channel


def test_a_channel_that_is_its_host_still_counts():
    from ingest_audio import _extract_known_speakers

    assert _extract_known_speakers("An Episode", None, "Lex Fridman") == ["Lex Fridman"]


def test_a_non_latin_name_is_rejected_by_design_not_by_oversight():
    """`_looks_like_name` tests word[0].isupper(), and kanji and kana have no case,
    so a Japanese-script candidate is rejected and the roster comes back EMPTY.

    That is the intended behaviour, not a bug to fix. Relaxing the test to "not
    lowercase" would admit any capitalised fragment - "2024 Update" would become a
    person - and a false name is the expensive failure: it reaches the graph as a
    node somebody has to find and merge later. Finding it is the hard part; the
    corpus was once unanimous on a speaker's spelling and unanimously wrong.

    An empty roster costs a reviewer some typing instead. If non-Latin sources ever
    arrive, add a script-aware check rather than loosening this one.
    """
    from ingest_audio import _extract_known_speakers

    assert _extract_known_speakers("高野 定雄: コスモアイル羽咋", None, None) == []
    assert _extract_known_speakers("Interview (Ft. 高野 定雄)", None, None) == []
    # The romanised form of the same person is picked up normally.
    assert _extract_known_speakers("Josen Takano: Cosmo Isle Hakui", None, None) == [
        "Josen Takano"
    ]


def test_a_capitalised_non_name_stays_out():
    """The concrete cost of relaxing the script check above."""
    from ingest_audio import _extract_known_speakers

    assert _extract_known_speakers("2024 Update", None, None) == []
