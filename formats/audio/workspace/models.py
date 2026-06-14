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
class TimedSentence:
    """A single sentence with its start timestamp.

    ``words`` holds per-word timings when the pipeline runs with word-level
    timestamps retained (the v2/word path); it is None in the default
    sentence-only path.
    """

    time: float  # seconds
    text: str
    words: list[Word] | None = None


@dataclass
class Turn:
    """A speaker turn - aligned transcription attributed to a speaker."""

    speaker: str
    sentences: list[TimedSentence]


def format_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS (truncates fractional seconds)."""
    total = int(seconds)
    hours = total // 3600
    minutes = (total % 3600) // 60
    secs = total % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_time_precise(seconds: float) -> str:
    """Format seconds as HH:MM:SS.D (one decimal place, fixed 10 chars)."""
    total_int = int(seconds)
    tenths = int((seconds - total_int) * 10)
    hours = total_int // 3600
    minutes = (total_int % 3600) // 60
    secs = total_int % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{tenths}"


def detect_source_type(mime_type: str) -> str:
    """Determine record source_type from MIME type. Returns 'audio' or 'video'."""
    if mime_type.startswith("video/"):
        return "video"
    return "audio"
