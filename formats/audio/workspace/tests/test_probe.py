"""Tests for the audio file probing module."""

from unittest.mock import patch, MagicMock

from probe import probe


def _mock_ffprobe_output(format_data, streams_data):
    """Build a mock ffprobe JSON response."""
    import json

    output = json.dumps({"format": format_data, "streams": streams_data})
    return MagicMock(returncode=0, stdout=output)


@patch("probe.subprocess.run")
def test_probe_opus_in_webm(mock_run, tmp_path):
    audio = tmp_path / "test.webm"
    audio.write_bytes(b"x" * 12345)

    mock_run.return_value = _mock_ffprobe_output(
        format_data={"format_name": "matroska,webm", "bit_rate": "128000"},
        streams_data=[
            {
                "codec_type": "audio",
                "codec_name": "opus",
                "sample_rate": "48000",
                "channels": 2,
            }
        ],
    )

    result = probe(audio)
    assert result["codec"] == "opus"
    assert result["container"] == "matroska"
    assert result["sample_rate"] == 48000
    assert result["bitrate"] == 128000
    assert result["channels"] == 2
    assert result["size_bytes"] == 12345


@patch("probe.subprocess.run")
def test_probe_mp3(mock_run, tmp_path):
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"x" * 5000)

    mock_run.return_value = _mock_ffprobe_output(
        format_data={"format_name": "mp3", "bit_rate": "192000"},
        streams_data=[
            {
                "codec_type": "audio",
                "codec_name": "mp3",
                "sample_rate": "44100",
                "channels": 2,
            }
        ],
    )

    result = probe(audio)
    assert result["codec"] == "mp3"
    assert result["container"] == "mp3"
    assert result["bitrate"] == 192000


@patch("probe.subprocess.run")
def test_probe_falls_back_to_stream_bitrate(mock_run, tmp_path):
    audio = tmp_path / "test.opus"
    audio.write_bytes(b"x" * 1000)

    mock_run.return_value = _mock_ffprobe_output(
        format_data={"format_name": "ogg"},
        streams_data=[
            {
                "codec_type": "audio",
                "codec_name": "opus",
                "sample_rate": "48000",
                "channels": 2,
                "bit_rate": "96000",
            }
        ],
    )

    result = probe(audio)
    assert result["bitrate"] == 96000


@patch("probe.subprocess.run")
def test_probe_returns_size_when_ffprobe_fails(mock_run, tmp_path):
    audio = tmp_path / "test.opus"
    audio.write_bytes(b"x" * 999)

    mock_run.return_value = MagicMock(returncode=1, stdout="")
    result = probe(audio)
    assert result["size_bytes"] == 999
    assert result["codec"] is None
    assert result["container"] is None


@patch("probe.subprocess.run")
def test_probe_handles_missing_audio_stream(mock_run, tmp_path):
    audio = tmp_path / "test.bin"
    audio.write_bytes(b"x" * 100)

    mock_run.return_value = _mock_ffprobe_output(
        format_data={"format_name": "wav"},
        streams_data=[],
    )

    result = probe(audio)
    assert result["codec"] is None
    assert result["container"] == "wav"
    assert result["size_bytes"] == 100
