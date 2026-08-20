"""yt-dlp fetcher - downloads audio from video platforms."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
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

# The last human-readable reason a yt-dlp fetch failed, set by _download and read
# by the acquire loop so the ingest error says WHY (a 403 block, a private video,
# a format gate) rather than a bare "could not fetch".
last_error: str | None = None

# yt-dlp's own stderr, mapped to a plain reason. Ordered most-specific first.
_ERROR_REASONS = [
    (
        re.compile(r"confirm you.?re not a bot|not a bot", re.I),
        "YouTube bot check - needs cookies from a signed-in session",
    ),
    (
        re.compile(r"HTTP Error 403|403:\s*Forbidden", re.I),
        "YouTube blocked the media download (HTTP 403) - the no-token client is "
        "being rate-limited; needs a PO token or cookies",
    ),
    (
        re.compile(r"experiment that applies DRM|DRM protected|DRM", re.I),
        "YouTube applied a DRM experiment to the client used - another client or "
        "cookies may still work",
    ),
    (
        re.compile(r"Requested format is not available", re.I),
        "no downloadable audio format was offered (PO-token / format gating)",
    ),
    (
        re.compile(r"Private video", re.I),
        "the video is private",
    ),
    (
        re.compile(r"members-only|join this channel", re.I),
        "the video is members-only (needs a signed-in, subscribed session)",
    ),
    (
        re.compile(r"confirm your age|age-restricted|inappropriate for some", re.I),
        "the video is age-restricted (needs a signed-in session)",
    ),
    (
        re.compile(r"not available in your country|geo|geo-?restrict", re.I),
        "the video is geo-blocked",
    ),
    (
        re.compile(r"This live event|premieres in|will begin", re.I),
        "a livestream or premiere that has not started yet",
    ),
    (
        re.compile(
            r"Video unavailable|has been removed|no longer available|terminated", re.I
        ),
        "the video is unavailable or has been removed",
    ),
]


def _classify_error(stderr: str) -> str:
    """Map yt-dlp's stderr to a plain failure reason. Falls back to the last
    ERROR line yt-dlp printed, so the caller always has something specific."""
    for pattern, reason in _ERROR_REASONS:
        if pattern.search(stderr):
            return reason
    for line in reversed(stderr.splitlines()):
        stripped = line.strip()
        if stripped.startswith("ERROR"):
            return re.sub(r"^ERROR:\s*", "", stripped)[:200]
    return "yt-dlp failed with no error output"


def _auth_args() -> list[str]:
    """yt-dlp arguments that get past YouTube's download gating.

    YouTube now requires a proof-of-origin (PO) token to download media; the one
    client that did not (`android_vr`) is being 403-blocked. A signed-in cookies
    file is the reliable fix - it returns full formats and is not rate-limited -
    so point `INGEST_YTDLP_COOKIES` at a Netscape-format cookies.txt to use it.
    The Node runtime lets yt-dlp solve the signature challenge; with the bgutil
    PO-token plugin installed it is used automatically, no flag needed.
    """
    args = [
        "--js-runtimes",
        "node",
        # The signature/n-challenge solver. WITHOUT IT yt-dlp cannot sign media
        # URLs and every format 403s - it warns "n challenge solving failed" and
        # then presents the result as "requested format is not available", which
        # reads like the video has no audio rather than like a broken toolchain.
        "--remote-components",
        "ejs:github",
    ]
    cookies = os.environ.get("INGEST_YTDLP_COOKIES", "").strip()
    if cookies and Path(cookies).is_file():
        args += ["--cookies", cookies]
    # The bgutil PO-token provider in script mode: a self-contained Node script
    # (baked into the image) that mints the token yt-dlp needs, with no separate
    # service to keep running. Used automatically when present.
    pot_script = os.environ.get(
        "INGEST_YTDLP_POT_SCRIPT", "/opt/bgutil/server/build/generate_once.js"
    )
    if Path(pot_script).is_file():
        args += [
            "--extractor-args",
            f"youtubepot-bgutilscript:script_path={pot_script}",
        ]
    # THE CLIENT MATTERS, and only once a PO token is actually being minted.
    # Measured 2026-08-20 against a video that had been failing for days: with the
    # token working, `tv_simply` and `android` download while `web`, `mweb` and
    # `web_safari` return no media formats at all (YouTube serves them SABR, which
    # has no fetchable URL). Tested before the token was minting, every client
    # looks equally dead - which is how "YouTube has blocked everything" gets
    # concluded from a half-configured toolchain.
    #
    # `formats=missing_pot` keeps formats yt-dlp would otherwise drop for lacking
    # a token. Combined with an actual token, they download.
    client = os.environ.get("INGEST_YTDLP_CLIENT", "tv_simply").strip()
    args += ["--extractor-args", f"youtube:player_client={client};formats=missing_pot"]
    return args


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
            *_auth_args(),
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
        # yt-dlp's own message is the ONLY thing that says why - age gate, bot
        # check, region block, a stale extractor. Discarding it left the caller
        # emitting "could not fetch this video-platform URL", which is a restatement
        # of the exit code and not a diagnosis: two failed attempts on one video
        # recorded that string twice and nothing else, so the cause had to be
        # reproduced by hand afterwards.
        global last_error
        stderr = (result.stderr or b"").decode("utf-8", "replace").strip()
        for line in stderr.splitlines()[-8:]:
            print(f"  yt-dlp: {line}", file=sys.stderr)
        last_error = _classify_error(stderr)
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
