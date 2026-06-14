"""Align transcription segments to speaker diarisation segments."""

from __future__ import annotations

import re

from models import Segment, SpeakerSegment, TimedSentence, Turn

# Tokens that end in a period but do not end a sentence. Lower-cased, period
# stripped. Extend this set as transcripts surface new ones. Dotted acronyms
# (U.S., e.g., a.m.) and single-letter initials (George W.) are matched by
# pattern in _ends_with_abbreviation, so they don't need listing here.
_ABBREVIATIONS = {
    # honorifics / titles
    "dr",
    "mr",
    "mrs",
    "ms",
    "mx",
    "prof",
    "sr",
    "jr",
    "rev",
    "fr",
    "hon",
    "st",
    # ranks / roles
    "sen",
    "rep",
    "gov",
    "gen",
    "col",
    "lt",
    "sgt",
    "capt",
    "cmdr",
    "adm",
    "maj",
    "cpl",
    "det",
    "supt",
    "pres",
    # common / business
    "etc",
    "vs",
    "al",
    "inc",
    "ltd",
    "co",
    "corp",
    "vol",
    "fig",
    "dept",
    "est",
}

# A dotted acronym or Latin abbreviation: letters joined by periods, e.g.
# "U.S", "F.B.I", "e.g", "a.m" (trailing period already stripped by caller).
_DOTTED_ACRONYM_RE = re.compile(r"[A-Za-z](?:\.[A-Za-z])+$")


def _ends_with_abbreviation(fragment: str) -> bool:
    """True if a split fragment ends in an abbreviation/initial rather than a
    real sentence boundary, so it should be merged with the next fragment."""
    tokens = fragment.split()
    if not tokens:
        return False
    core = tokens[-1].rstrip(".")
    if not core:
        return False
    if core.lower() in _ABBREVIATIONS:
        return True
    if len(core) == 1 and core.isalpha():  # single-letter initial, e.g. "W."
        return True
    return bool(_DOTTED_ACRONYM_RE.fullmatch(core))


def _merge_abbreviation_splits(fragments: list[str]) -> list[str]:
    """Re-join fragments that the punctuation split wrongly broke at an
    abbreviation (e.g. "Dr." + "Smith was here." -> "Dr. Smith was here.")."""
    merged: list[str] = []
    buffer = ""
    for fragment in fragments:
        buffer = f"{buffer} {fragment}" if buffer else fragment
        if _ends_with_abbreviation(fragment):
            continue
        merged.append(buffer)
        buffer = ""
    if buffer:
        merged.append(buffer)
    return merged


def _split_segment_into_sentences(
    segment: Segment, keep_words: bool = False
) -> list[TimedSentence]:
    """Split a transcription segment into individual sentences using word timestamps.

    If the segment contains multiple sentences (detected by sentence-ending
    punctuation), splits them and assigns each sentence the timestamp of its
    first word. If the segment is a single sentence, returns it as-is. When
    ``keep_words`` is set, each returned sentence also carries the slice of
    per-word timings it was built from (for word-level/v2 output).
    """
    text = segment.text.strip()
    words = segment.words

    if not words:
        return [TimedSentence(time=segment.start, text=text)]

    # Split text into sentences on . ? !
    # Keep the punctuation with the sentence
    sentence_texts = re.split(r"(?<=[.?!])\s+", text)
    sentence_texts = [s.strip() for s in sentence_texts if s.strip()]
    # Re-join fragments split at an abbreviation/initial that isn't a real
    # sentence end ("Dr." | "Smith spoke." -> "Dr. Smith spoke.").
    sentence_texts = _merge_abbreviation_splits(sentence_texts)

    if len(sentence_texts) <= 1:
        return [
            TimedSentence(
                time=segment.start,
                text=text,
                words=list(words) if keep_words else None,
            )
        ]

    # Map each sentence to the timestamp of its first word
    sentences = []
    word_idx = 0

    for sentence_text in sentence_texts:
        # Find the first word of this sentence in the word list
        sentence_time = segment.start
        sentence_words_lower = sentence_text.lower().split()
        sentence_words = None

        if sentence_words_lower and word_idx < len(words):
            # Walk through words to find where this sentence starts
            first_word = sentence_words_lower[0].strip(".,!?;:'\"")
            for i in range(word_idx, len(words)):
                if words[i].text.lower().strip(".,!?;:'\"") == first_word:
                    sentence_time = words[i].start
                    # Advance word_idx past this sentence's words
                    end_idx = i + len(sentence_words_lower)
                    if keep_words:
                        sentence_words = words[i:end_idx]
                    word_idx = end_idx
                    break

        sentences.append(
            TimedSentence(time=sentence_time, text=sentence_text, words=sentence_words)
        )

    return sentences


def align(
    segments: list[Segment],
    speaker_segments: list[SpeakerSegment],
    keep_words: bool = False,
) -> list[Turn]:
    """Align transcription segments to speakers and group into turns.

    Each transcription segment is split into individual sentences and assigned
    to the speaker who covers the majority of its duration. Consecutive
    sentences from the same speaker are grouped into turns. ``keep_words``
    retains per-word timings on each sentence for word-level/v2 output.
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
        sentences = _split_segment_into_sentences(segment, keep_words=keep_words)

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
