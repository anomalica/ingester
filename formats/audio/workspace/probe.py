"""Audio file probing via ffprobe."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def probe(audio_path: Path) -> dict:
    """Probe an audio file with ffprobe and return key characteristics.

    Returns a dict with:
        codec: e.g. 'opus', 'mp3', 'aac'
        container: e.g. 'webm', 'mp3', 'mp4'
        sample_rate: integer Hz
        bitrate: integer bits per second (None if not reported)
        size_bytes: file size in bytes
        channels: integer number of audio channels

    All fields default to None if probing fails or the field is unavailable.
    """
    result = {
        "codec": None,
        "container": None,
        "sample_rate": None,
        "bitrate": None,
        "size_bytes": audio_path.stat().st_size,
        "channels": None,
    }

    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(audio_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode != 0:
            return result
        data = json.loads(proc.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return result

    fmt = data.get("format", {})
    container = fmt.get("format_name")
    if container and "," in container:
        container = container.split(",")[0]
    result["container"] = container

    if fmt.get("bit_rate"):
        try:
            result["bitrate"] = int(fmt["bit_rate"])
        except (ValueError, TypeError):
            pass

    streams = data.get("streams", [])
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if audio_stream:
        result["codec"] = audio_stream.get("codec_name")
        if audio_stream.get("sample_rate"):
            try:
                result["sample_rate"] = int(audio_stream["sample_rate"])
            except (ValueError, TypeError):
                pass
        if audio_stream.get("channels"):
            try:
                result["channels"] = int(audio_stream["channels"])
            except (ValueError, TypeError):
                pass
        # Stream-level bitrate as fallback if format-level not available
        if result["bitrate"] is None and audio_stream.get("bit_rate"):
            try:
                result["bitrate"] = int(audio_stream["bit_rate"])
            except (ValueError, TypeError):
                pass

    return result
