"""Align transcribed words to speaker diarisation segments."""

from __future__ import annotations

from models import Segment, SpeakerSegment, Turn


def align(
    segments: list[Segment], speaker_segments: list[SpeakerSegment]
) -> list[Turn]:
    """Align transcribed words to speaker segments.

    For each word, find which speaker segment it falls within (by timestamp
    overlap at the word's midpoint). Consecutive words from the same speaker
    are grouped into turns. Words falling in gaps between speaker segments
    are assigned to the nearest segment.
    """
    if not segments or not speaker_segments:
        return []

    words = []
    for segment in segments:
        words.extend(segment.words)

    if not words:
        return []

    turns: list[Turn] = []
    current_speaker: str | None = None
    current_words: list[str] = []
    current_start: float = 0.0

    for word in words:
        word_mid = (word.start + word.end) / 2
        speaker = _find_speaker(word_mid, speaker_segments)

        if speaker != current_speaker:
            if current_speaker is not None and current_words:
                turns.append(
                    Turn(
                        speaker=current_speaker,
                        time=current_start,
                        text=" ".join(current_words),
                    )
                )
            current_speaker = speaker
            current_words = [word.text]
            current_start = word.start
        else:
            current_words.append(word.text)

    if current_speaker is not None and current_words:
        turns.append(
            Turn(
                speaker=current_speaker,
                time=current_start,
                text=" ".join(current_words),
            )
        )

    return turns


def _find_speaker(time: float, speaker_segments: list[SpeakerSegment]) -> str:
    """Find which speaker is active at a given time.

    Checks for direct overlap first. If no segment covers this time,
    returns the nearest segment's speaker.
    """
    for seg in speaker_segments:
        if seg.start <= time <= seg.end:
            return seg.speaker

    return min(
        speaker_segments,
        key=lambda s: min(abs(s.start - time), abs(s.end - time)),
    ).speaker
