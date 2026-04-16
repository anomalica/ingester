"""Align transcription segments to speaker diarisation segments."""

from __future__ import annotations

import re

from models import Segment, SpeakerSegment, TimedSentence, Turn


def _split_segment_into_sentences(segment: Segment) -> list[TimedSentence]:
    """Split a transcription segment into individual sentences using word timestamps.

    If the segment contains multiple sentences (detected by sentence-ending
    punctuation), splits them and assigns each sentence the timestamp of its
    first word. If the segment is a single sentence, returns it as-is.
    """
    text = segment.text.strip()
    words = segment.words

    if not words:
        return [TimedSentence(time=segment.start, text=text)]

    # Split text into sentences on . ? !
    # Keep the punctuation with the sentence
    sentence_texts = re.split(r"(?<=[.?!])\s+", text)
    sentence_texts = [s.strip() for s in sentence_texts if s.strip()]

    if len(sentence_texts) <= 1:
        return [TimedSentence(time=segment.start, text=text)]

    # Map each sentence to the timestamp of its first word
    sentences = []
    word_idx = 0

    for sentence_text in sentence_texts:
        # Find the first word of this sentence in the word list
        sentence_time = segment.start
        sentence_words_lower = sentence_text.lower().split()

        if sentence_words_lower and word_idx < len(words):
            # Walk through words to find where this sentence starts
            first_word = sentence_words_lower[0].strip(".,!?;:'\"")
            for i in range(word_idx, len(words)):
                if words[i].text.lower().strip(".,!?;:'\"") == first_word:
                    sentence_time = words[i].start
                    # Advance word_idx past this sentence's words
                    word_idx = i + len(sentence_words_lower)
                    break

        sentences.append(TimedSentence(time=sentence_time, text=sentence_text))

    return sentences


def align(
    segments: list[Segment], speaker_segments: list[SpeakerSegment]
) -> list[Turn]:
    """Align transcription segments to speakers and group into turns.

    Each transcription segment is split into individual sentences and assigned
    to the speaker who covers the majority of its duration. Consecutive
    sentences from the same speaker are grouped into turns.
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
        sentences = _split_segment_into_sentences(segment)

        if speaker != current_speaker:
            if current_speaker is not None and current_sentences:
                turns.append(Turn(speaker=current_speaker, sentences=current_sentences))
            current_speaker = speaker
            current_sentences = sentences
        else:
            current_sentences.extend(sentences)

    if current_speaker is not None and current_sentences:
        turns.append(Turn(speaker=current_speaker, sentences=current_sentences))

    return turns


def _majority_speaker(segment: Segment, speaker_segments: list[SpeakerSegment]) -> str:
    """Find which speaker covers the majority of a transcription segment."""
    overlap: dict[str, float] = {}

    for ss in speaker_segments:
        start = max(segment.start, ss.start)
        end = min(segment.end, ss.end)
        if start < end:
            overlap[ss.speaker] = overlap.get(ss.speaker, 0.0) + (end - start)

    if overlap:
        return max(overlap, key=overlap.get)

    seg_mid = (segment.start + segment.end) / 2
    return min(
        speaker_segments,
        key=lambda s: min(abs(s.start - seg_mid), abs(s.end - seg_mid)),
    ).speaker
