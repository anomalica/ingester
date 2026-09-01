import json
from unittest.mock import MagicMock, patch

from fetch.ytdlp import (
    _classify_error,
    _download,
    _is_supported,
    fetch,
    is_video_platform,
)


def test_classify_token_provider_failure_points_at_the_toolchain():
    """A missing/unreachable PO-token provider makes yt-dlp warn 'n challenge
    solving failed' and then fail 'Requested format is not available' - which reads
    as a YouTube refusal. It must instead be classified as a toolchain fault, so the
    token-failure signature is matched BEFORE the generic format-gating message."""
    stderr = (
        "WARNING: [youtube] n challenge solving failed: Some formats may be missing\n"
        "WARNING: Only images are available for download\n"
        "ERROR: Requested format is not available\n"
    )
    reason = _classify_error(stderr)
    assert "provider unreachable" in reason.lower()
    assert "toolchain" in reason.lower()
    assert "format gating" not in reason.lower()


def test_classify_genuine_format_gate_without_token_failure_is_unchanged():
    reason = _classify_error("ERROR: Requested format is not available\n")
    assert "PO-token / format gating" in reason


def _failed_run(stderr: bytes = b"ERROR: unable to download video data"):
    """A failed subprocess result whose stderr decodes to a real string.

    _download now classifies the failure by reading stderr, so a bare
    MagicMock(returncode=1) leaves it matching regexes against a mock object.
    """
    return MagicMock(returncode=1, stderr=stderr)


def test_is_supported_youtube_watch():
    assert _is_supported("https://www.youtube.com/watch?v=abc123")


def test_is_supported_youtube_short_url():
    assert _is_supported("https://youtu.be/abc123")


def test_is_supported_youtube_shorts():
    assert _is_supported("https://www.youtube.com/shorts/abc123")


def test_is_supported_youtube_live():
    assert _is_supported("https://www.youtube.com/live/abc123")


def test_is_supported_archive_org():
    assert _is_supported("https://archive.org/details/youtube-_zjzltFIpe8")


def test_is_supported_rejects_news_article():
    assert not _is_supported("https://www.nytimes.com/2017/article")


def test_is_supported_rejects_pdf_url():
    assert not _is_supported("https://example.com/document.pdf")


def test_is_supported_rejects_generic_url():
    assert not _is_supported("https://thedebrief.org/some-article")


def test_is_video_platform_youtube():
    assert is_video_platform("https://www.youtube.com/watch?v=abc123")
    assert is_video_platform("https://youtu.be/abc123")


def test_is_video_platform_excludes_archive_org():
    # archive.org is yt-dlp-supported but not a strict video platform, so an
    # HTML fallback stays allowed for it.
    assert not is_video_platform("https://archive.org/details/some-item")


def test_is_video_platform_rejects_generic_url():
    assert not is_video_platform("https://www.nytimes.com/article")


def test_fetch_returns_none_for_unsupported_url():
    result = fetch("https://www.nytimes.com/article")
    assert result is None


@patch("fetch.ytdlp._download")
def test_fetch_returns_audio_bytes_mp3(mock_download, tmp_path):
    audio_file = tmp_path / "audio.mp3"
    audio_file.write_bytes(b"fake mp3 content")
    mock_download.return_value = (audio_file, None)

    result = fetch("https://www.youtube.com/watch?v=abc123")
    assert result is not None
    content, content_type, metadata = result
    assert content == b"fake mp3 content"
    assert content_type == "audio/mpeg"
    assert metadata is None


@patch("fetch.ytdlp._download")
def test_fetch_returns_audio_bytes_and_metadata_opus(mock_download, tmp_path):
    audio_file = tmp_path / "audio.opus"
    audio_file.write_bytes(b"fake opus content")
    mock_download.return_value = (
        audio_file,
        {"title": "Test Video", "source_id": "youtube:abc123"},
    )

    result = fetch("https://youtu.be/abc123")
    assert result is not None
    content, content_type, metadata = result
    assert content == b"fake opus content"
    assert content_type == "audio/opus"
    assert metadata["title"] == "Test Video"
    assert metadata["source_id"] == "youtube:abc123"


@patch("fetch.ytdlp._download")
def test_fetch_returns_none_when_download_fails(mock_download):
    mock_download.return_value = (None, None)
    result = fetch("https://www.youtube.com/watch?v=abc123")
    assert result is None


@patch("fetch.ytdlp.subprocess.run")
def test_download_calls_ytdlp_with_info_json(mock_run, tmp_path):
    mock_run.return_value = _failed_run()
    _download("https://www.youtube.com/watch?v=abc123", tmp_path)

    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "yt-dlp"
    assert "--extract-audio" in cmd
    assert "--no-playlist" in cmd
    # The metadata is captured in the SAME call as the download so they cannot
    # diverge.
    assert "--write-info-json" in cmd
    assert "https://www.youtube.com/watch?v=abc123" in cmd


@patch("fetch.ytdlp.subprocess.run")
def test_download_returns_none_on_nonzero_exit(mock_run, tmp_path):
    mock_run.return_value = _failed_run()
    audio, metadata = _download("https://www.youtube.com/watch?v=abc123", tmp_path)
    assert audio is None
    assert metadata is None


@patch("fetch.ytdlp.subprocess.run")
def test_download_returns_file_and_metadata(mock_run, tmp_path):
    def side_effect(*args, **kwargs):
        (tmp_path / "audio.opus").write_bytes(b"audio data")
        (tmp_path / "audio.info.json").write_text(
            json.dumps({"title": "Ep 58", "extractor": "youtube", "id": "wX3whEVHr3g"})
        )
        return MagicMock(returncode=0)

    mock_run.side_effect = side_effect
    audio, metadata = _download("https://www.youtube.com/watch?v=wX3whEVHr3g", tmp_path)
    assert audio is not None
    assert audio.name == "audio.opus"
    assert audio.read_bytes() == b"audio data"
    assert metadata["title"] == "Ep 58"
    # source_id derived from extractor:id in the same call as the download.
    assert metadata["source_id"] == "youtube:wX3whEVHr3g"


@patch("fetch.ytdlp.subprocess.run")
def test_download_metadata_none_when_no_info_json(mock_run, tmp_path):
    def side_effect(*args, **kwargs):
        (tmp_path / "audio.opus").write_bytes(b"audio data")
        return MagicMock(returncode=0)

    mock_run.side_effect = side_effect
    audio, metadata = _download("https://www.youtube.com/watch?v=abc123", tmp_path)
    assert audio is not None
    assert metadata is None
