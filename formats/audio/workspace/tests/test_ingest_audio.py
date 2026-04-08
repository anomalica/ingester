import json
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


@patch("ingest_audio.diarise", return_value=MOCK_SPEAKER_SEGMENTS)
@patch("ingest_audio.transcribe", return_value=MOCK_SEGMENTS)
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
    assert "speaker: Speaker 1" in content
    assert "speaker: Speaker 2" in content
    assert "Hello there" in content
    assert "I am fine thanks" in content


@patch("ingest_audio.diarise", return_value=MOCK_SPEAKER_SEGMENTS)
@patch("ingest_audio.transcribe", return_value=MOCK_SEGMENTS)
def test_run_writes_metadata(mock_transcribe, mock_diarise, tmp_path):
    import ingest_audio

    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_audio.run(staging, output, force=False)

    meta_files = list((output / "store").glob("*.meta.json"))
    assert len(meta_files) == 1
    meta = json.loads(meta_files[0].read_text())
    assert "input_hash" in meta
    assert meta["source_url"] == "https://example.com/ep.mp3"
    assert "duration_seconds" in meta


@patch("ingest_audio.diarise", return_value=MOCK_SPEAKER_SEGMENTS)
@patch("ingest_audio.transcribe", return_value=MOCK_SEGMENTS)
def test_run_creates_symlink(mock_transcribe, mock_diarise, tmp_path):
    import ingest_audio

    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_audio.run(staging, output, force=False)

    links = list((output / "records").glob("*.md"))
    assert len(links) == 1
    assert links[0].is_symlink()
    assert "audio" in links[0].name


@patch("ingest_audio.diarise", return_value=MOCK_SPEAKER_SEGMENTS)
@patch("ingest_audio.transcribe", return_value=MOCK_SEGMENTS)
def test_run_skips_existing(mock_transcribe, mock_diarise, tmp_path):
    import ingest_audio

    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_audio.run(staging, output, force=False)
    ingest_audio.run(staging, output, force=False)

    md_files = list((output / "store").glob("*.md"))
    assert len(md_files) == 1
    assert mock_transcribe.call_count == 1


@patch("ingest_audio.diarise", return_value=MOCK_SPEAKER_SEGMENTS)
@patch("ingest_audio.transcribe", return_value=MOCK_SEGMENTS)
def test_run_force_reprocesses(mock_transcribe, mock_diarise, tmp_path):
    import ingest_audio

    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_audio.run(staging, output, force=False)
    ingest_audio.run(staging, output, force=True)

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


@patch("ingest_audio.diarise", return_value=MOCK_SPEAKER_SEGMENTS)
@patch("ingest_audio.transcribe", return_value=MOCK_SEGMENTS)
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


@patch("ingest_audio.diarise", return_value=MOCK_SPEAKER_SEGMENTS)
@patch("ingest_audio.transcribe", return_value=MOCK_SEGMENTS)
def test_run_speaker_roster_in_frontmatter(mock_transcribe, mock_diarise, tmp_path):
    import ingest_audio

    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_audio.run(staging, output, force=False)

    md_files = list((output / "store").glob("*.md"))
    content = md_files[0].read_text()
    assert "speakers:" in content
    assert "id: Speaker 1" in content
    assert "name: Unknown" in content
    assert "first_appearance:" in content


@patch("ingest_audio.diarise", return_value=MOCK_SPEAKER_SEGMENTS)
@patch("ingest_audio.transcribe", return_value=MOCK_SEGMENTS)
def test_run_duration_in_frontmatter(mock_transcribe, mock_diarise, tmp_path):
    import ingest_audio

    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_audio.run(staging, output, force=False)

    md_files = list((output / "store").glob("*.md"))
    content = md_files[0].read_text()
    assert "duration:" in content


@patch("ingest_audio.diarise", return_value=MOCK_SPEAKER_SEGMENTS)
@patch("ingest_audio.transcribe", return_value=MOCK_SEGMENTS)
def test_run_time_annotations_formatted(mock_transcribe, mock_diarise, tmp_path):
    import ingest_audio

    staging = _create_staging(tmp_path)
    output = tmp_path / "output"
    ingest_audio.run(staging, output, force=False)

    md_files = list((output / "store").glob("*.md"))
    content = md_files[0].read_text()
    assert "time: 00:00:00" in content
    assert "time: 00:00:02" in content


@patch("ingest_audio.diarise", return_value=MOCK_SPEAKER_SEGMENTS)
@patch("ingest_audio.transcribe", return_value=[])
def test_run_fails_on_empty_transcription(mock_transcribe, mock_diarise, tmp_path):
    import ingest_audio

    staging = _create_staging(tmp_path)
    output = tmp_path / "output"

    assert ingest_audio.run(staging, output, force=False) != 0
