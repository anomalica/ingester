"""Data types and utilities for the audio ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Word:
    """A single transcribed word with timestamps."""

    text: str
    start: float  # seconds
    end: float  # seconds


@dataclass
class Segment:
    """A transcription segment with word-level timestamps."""

    text: str
    start: float  # seconds
    end: float  # seconds
    words: list[Word]


@dataclass
class SpeakerSegment:
    """A speaker diarisation segment."""

    speaker: str  # "SPEAKER_00", "SPEAKER_01", etc.
    start: float  # seconds
    end: float  # seconds


@dataclass
class Turn:
    """A speaker turn - aligned transcription attributed to a speaker."""

    speaker: str  # "SPEAKER_00", etc.
    time: float  # seconds (start of turn)
    text: str  # transcript text for this turn


def format_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS (truncates fractional seconds)."""
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def detect_source_type(mime_type: str) -> str:
    """Determine record source_type from MIME type. Returns 'audio' or 'video'."""
    if mime_type.startswith("video/"):
        return "video"
    return "audio"
