"""yt-dlp fetcher - downloads audio from video platforms."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

# Video-platform URLs where scraping an HTML page shell is never the right
# answer: if yt-dlp cannot fetch the media, the ingest must fail rather than
# fall through to a fetcher that would produce a bogus web record.
VIDEO_PLATFORM_PATTERNS = [
    re.compile(r"https?://(?:www\.)?youtube\.com/watch\?"),
    re.compile(r"https?://youtu\.be/"),
    re.compile(r"https?://(?:www\.)?youtube\.com/shorts/"),
    re.compile(r"https?://(?:www\.)?youtube\.com/live/"),
]

# archive.org/details hosts mixed content (video, audio, text), so a non-yt-dlp
# fallback can be legitimate - it is supported by yt-dlp but not treated as a
# strict video platform.
SUPPORTED_PATTERNS = VIDEO_PLATFORM_PATTERNS + [
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
    """Check if the URL matches a known video platform yt-dlp can fetch."""
    return any(pattern.search(url) for pattern in SUPPORTED_PATTERNS)


def is_video_platform(url: str) -> bool:
    """True for a strict video-platform URL (youtube family) where an HTML
    fallback is never correct. The acquire loop uses this to fetch such URLs
    with yt-dlp only, failing cleanly instead of scraping the page shell."""
    return any(pattern.search(url) for pattern in VIDEO_PLATFORM_PATTERNS)


def _parse_info_json(path: Path) -> dict | None:
    """Read a yt-dlp .info.json and add a stable '{extractor}:{id}' source_id."""
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, ValueError, OSError):
        return None
    extractor = data.get("extractor")
    video_id = data.get("id")
    if extractor and video_id:
        data["source_id"] = f"{extractor}:{video_id}"
    return data


def _download(url: str, output_dir: Path) -> tuple[Path | None, dict | None]:
    """Download the audio track and its metadata in a SINGLE yt-dlp call.

    One invocation with --write-info-json means the metadata cannot diverge
    from the download: if the audio lands, the info JSON lands with it. Two
    separate calls (a --dump-json for metadata and a --extract-audio for the
    file) previously let the metadata call fail while the download succeeded,
    dropping the title (record fell back to the asset name) and source_id.

    Returns (audio_path, metadata), or (None, None) on failure. metadata is
    None only if the info JSON is somehow absent despite a successful download.
    """
    output_template = str(output_dir / "audio.%(ext)s")
    result = subprocess.run(
        [
            "yt-dlp",
            "--extract-audio",
            "--no-playlist",
            "--no-overwrites",
            "--write-info-json",
            "--output",
            output_template,
            url,
        ],
        capture_output=True,
        timeout=TIMEOUT,
    )
    if result.returncode != 0:
        return None, None

    files = list(output_dir.iterdir())
    info_files = [f for f in files if f.name.endswith(".info.json")]
    audio_files = [
        f
        for f in files
        if f.name.startswith("audio.") and not f.name.endswith(".info.json")
    ]
    audio_file = audio_files[0] if audio_files else None
    metadata = _parse_info_json(info_files[0]) if info_files else None
    return audio_file, metadata


def fetch(url: str) -> tuple[bytes, str | None, dict | None] | None:
    """Download audio from a video platform via yt-dlp.

    Returns None immediately for unsupported URLs, and None if the download
    fails. On success returns (bytes, content_type, metadata), where metadata
    carries yt-dlp's extracted fields (title, upload_date, source_id, etc.).
    """
    if not _is_supported(url):
        return None

    with tempfile.TemporaryDirectory() as tmp_dir:
        audio_file, metadata = _download(url, Path(tmp_dir))
        if audio_file is None:
            return None

        content_type = EXTENSION_TO_MIME.get(
            audio_file.suffix.lower(), "application/octet-stream"
        )
        return (audio_file.read_bytes(), content_type, metadata)
