"""Align transcription segments to speaker diarisation segments."""

from __future__ import annotations

from models import Segment, SpeakerSegment, TimedSentence, Turn


def align(
    segments: list[Segment], speaker_segments: list[SpeakerSegment]
) -> list[Turn]:
    """Align transcription segments to speakers and group into turns.

    Each transcription segment (a natural phrase with punctuation) is assigned
    to the speaker who covers the majority of its duration. This preserves
    sentence boundaries and punctuation from the transcription, rather than
    splitting mid-sentence when diarisation boundaries don't align perfectly.

    Consecutive segments from the same speaker are grouped into turns.
    Each segment becomes a TimedSentence with its own timestamp.
    """
    if not segments or not speaker_segments:
        return []

    turns: list[Turn] = []
    current_speaker: str | None = None
    current_sentences: list[TimedSentence] = []

    for segment in segments:
        if not segment.text.strip():
            continue

        speaker = _majority_speaker(segment, speaker_segments)

        if speaker != current_speaker:
            if current_speaker is not None and current_sentences:
                turns.append(Turn(speaker=current_speaker, sentences=current_sentences))
            current_speaker = speaker
            current_sentences = [
                TimedSentence(time=segment.start, text=segment.text.strip())
            ]
        else:
            current_sentences.append(
                TimedSentence(time=segment.start, text=segment.text.strip())
            )

    if current_speaker is not None and current_sentences:
        turns.append(Turn(speaker=current_speaker, sentences=current_sentences))

    return turns


def _majority_speaker(segment: Segment, speaker_segments: list[SpeakerSegment]) -> str:
    """Find which speaker covers the majority of a transcription segment.

    Calculates the overlap duration between the segment and each speaker
    segment, and returns the speaker with the most total overlap.
    """
    overlap: dict[str, float] = {}

    for ss in speaker_segments:
        start = max(segment.start, ss.start)
        end = min(segment.end, ss.end)
        if start < end:
            overlap[ss.speaker] = overlap.get(ss.speaker, 0.0) + (end - start)

    if overlap:
        return max(overlap, key=overlap.get)

    # No overlap - find the nearest speaker segment
    seg_mid = (segment.start + segment.end) / 2
    return min(
        speaker_segments,
        key=lambda s: min(abs(s.start - seg_mid), abs(s.end - seg_mid)),
    ).speaker
