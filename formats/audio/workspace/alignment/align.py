"""Align transcription segments to speaker diarisation segments."""

from __future__ import annotations

from models import Segment, SpeakerSegment, Turn


def align(
    segments: list[Segment], speaker_segments: list[SpeakerSegment]
) -> list[Turn]:
    """Align transcription segments to speakers and group into turns.

    Each transcription segment (a natural phrase with punctuation) is assigned
    to the speaker who covers the majority of its duration. This preserves
    sentence boundaries and punctuation from the transcription, rather than
    splitting mid-sentence when diarisation boundaries don't align perfectly.

    Consecutive segments from the same speaker are grouped into turns,
    separated by newlines to preserve natural paragraph breaks.
    """
    if not segments or not speaker_segments:
        return []

    turns: list[Turn] = []
    current_speaker: str | None = None
    current_texts: list[str] = []
    current_start: float = 0.0

    for segment in segments:
        if not segment.text.strip():
            continue

        speaker = _majority_speaker(segment, speaker_segments)

        if speaker != current_speaker:
            if current_speaker is not None and current_texts:
                turns.append(
                    Turn(
                        speaker=current_speaker,
                        time=current_start,
                        text="\n".join(current_texts),
                    )
                )
            current_speaker = speaker
            current_texts = [segment.text.strip()]
            current_start = segment.start
        else:
            current_texts.append(segment.text.strip())

    if current_speaker is not None and current_texts:
        turns.append(
            Turn(
                speaker=current_speaker,
                time=current_start,
                text="\n".join(current_texts),
            )
        )

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
