from unittest.mock import patch, MagicMock

from fetch.ytdlp import fetch, _is_supported, _download


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


def test_fetch_returns_none_for_unsupported_url():
    result = fetch("https://www.nytimes.com/article")
    assert result is None


@patch("fetch.ytdlp._download")
def test_fetch_returns_audio_bytes_mp3(mock_download, tmp_path):
    audio_file = tmp_path / "audio.mp3"
    audio_file.write_bytes(b"fake mp3 content")
    mock_download.return_value = audio_file

    result = fetch("https://www.youtube.com/watch?v=abc123")
    assert result is not None
    content, content_type = result
    assert content == b"fake mp3 content"
    assert content_type == "audio/mpeg"


@patch("fetch.ytdlp._download")
def test_fetch_returns_audio_bytes_opus(mock_download, tmp_path):
    audio_file = tmp_path / "audio.opus"
    audio_file.write_bytes(b"fake opus content")
    mock_download.return_value = audio_file

    result = fetch("https://youtu.be/abc123")
    assert result is not None
    content, content_type = result
    assert content == b"fake opus content"
    assert content_type == "audio/opus"


@patch("fetch.ytdlp._download")
def test_fetch_returns_none_when_download_fails(mock_download):
    mock_download.return_value = None
    result = fetch("https://www.youtube.com/watch?v=abc123")
    assert result is None


@patch("fetch.ytdlp.subprocess.run")
def test_download_calls_ytdlp_with_correct_args(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=1)
    _download("https://www.youtube.com/watch?v=abc123", tmp_path)

    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "yt-dlp"
    assert "--extract-audio" in cmd
    assert "--no-playlist" in cmd
    assert "https://www.youtube.com/watch?v=abc123" in cmd


@patch("fetch.ytdlp.subprocess.run")
def test_download_returns_none_on_nonzero_exit(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=1)
    result = _download("https://www.youtube.com/watch?v=abc123", tmp_path)
    assert result is None


@patch("fetch.ytdlp.subprocess.run")
def test_download_returns_file_path(mock_run, tmp_path):
    def side_effect(*args, **kwargs):
        (tmp_path / "audio.opus").write_bytes(b"audio data")
        return MagicMock(returncode=0)

    mock_run.side_effect = side_effect
    result = _download("https://www.youtube.com/watch?v=abc123", tmp_path)
    assert result is not None
    assert result.name == "audio.opus"
    assert result.read_bytes() == b"audio data"
