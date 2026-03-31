"""yt-dlp fetcher - downloads audio from video platforms."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

SUPPORTED_PATTERNS = [
    re.compile(r"https?://(?:www\.)?youtube\.com/watch\?"),
    re.compile(r"https?://youtu\.be/"),
    re.compile(r"https?://(?:www\.)?youtube\.com/shorts/"),
    re.compile(r"https?://(?:www\.)?youtube\.com/live/"),
    re.compile(r"https?://(?:www\.)?archive\.org/details/"),
]

EXTENSION_TO_MIME = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".opus": "audio/opus",
    ".ogg": "audio/ogg",
    ".webm": "audio/webm",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
}

TIMEOUT = 600


def _is_supported(url: str) -> bool:
    """Check if the URL matches a known video platform."""
    return any(pattern.search(url) for pattern in SUPPORTED_PATTERNS)


def _download(url: str, output_dir: Path) -> Path | None:
    """Download audio track to output_dir via yt-dlp.

    Returns the path to the downloaded file, or None on failure.
    """
    output_template = str(output_dir / "audio.%(ext)s")
    result = subprocess.run(
        [
            "yt-dlp",
            "--extract-audio",
            "--no-playlist",
            "--no-overwrites",
            "--output",
            output_template,
            url,
        ],
        capture_output=True,
        timeout=TIMEOUT,
    )

    if result.returncode != 0:
        return None

    audio_files = [f for f in output_dir.iterdir() if f.name.startswith("audio.")]
    return audio_files[0] if audio_files else None


def fetch(url: str) -> tuple[bytes, str | None] | None:
    """Download audio from a video platform via yt-dlp.

    Returns None immediately for unsupported URLs. For supported URLs,
    downloads the audio track and returns (bytes, content_type).
    """
    if not _is_supported(url):
        return None

    with tempfile.TemporaryDirectory() as tmp_dir:
        audio_file = _download(url, Path(tmp_dir))
        if audio_file is None:
            return None

        content_type = EXTENSION_TO_MIME.get(
            audio_file.suffix.lower(), "application/octet-stream"
        )
        return (audio_file.read_bytes(), content_type)
