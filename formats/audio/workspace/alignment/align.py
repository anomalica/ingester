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


# A transcription segment can span a speaker change - an interjection lands
# mid-segment constantly in an interview - so a segment is first SPLIT at the
# points where its own words change speaker. Without that split the whole
# segment takes its majority speaker and the interjection is attributed to
# whoever was talking around it. Measured over three reviewed interviews
# (82,143 words), splitting cut misattributed words from 4.75% to 3.88%.
#
# A split must be worth making: a single word landing on the other speaker is
# usually diarisation jitter at a boundary, not a real turn. A run must reach
# both thresholds to stand on its own, otherwise it stays with the run before
# it. Values are the flat of the swept curve - below them turn count rises with
# no accuracy gain, above them real interjections start being swallowed again.
_MIN_SWITCH_WORDS = 4
_MIN_SWITCH_SECONDS = 0.8


def _speaker_runs(
    words: list, speaker_segments: list[SpeakerSegment]
) -> list[tuple[str, list]]:
    """Consecutive words sharing a speaker, with runs too short to be a real
    turn folded back into the run before them."""
    runs: list[list] = []
    for word in words:
        speaker = _word_speaker(word, speaker_segments)
        if runs and runs[-1][0] == speaker:
            runs[-1][1].append(word)
        else:
            runs.append([speaker, [word]])

    kept: list[list] = []
    for speaker, run_words in runs:
        duration = run_words[-1].end - run_words[0].start
        too_short = len(run_words) < _MIN_SWITCH_WORDS or duration < _MIN_SWITCH_SECONDS
        if kept and too_short:
            kept[-1][1].extend(run_words)
        else:
            kept.append([speaker, run_words])

    # Folding a run in can leave two neighbours with the same speaker.
    merged: list[list] = []
    for speaker, run_words in kept:
        if merged and merged[-1][0] == speaker:
            merged[-1][1].extend(run_words)
        else:
            merged.append([speaker, run_words])
    return [(speaker, run_words) for speaker, run_words in merged]


def _word_speaker(word, speaker_segments: list[SpeakerSegment]) -> str:
    """The speaker talking at the word's midpoint, else the nearest one."""
    midpoint = (word.start + word.end) / 2
    nearest = None
    nearest_gap = None
    for ss in speaker_segments:
        if ss.start <= midpoint <= ss.end:
            return ss.speaker
        gap = ss.start - midpoint if ss.start > midpoint else midpoint - ss.end
        if nearest_gap is None or gap < nearest_gap:
            nearest, nearest_gap = ss.speaker, gap
    return nearest


def split_on_speaker_change(
    segments: list[Segment], speaker_segments: list[SpeakerSegment]
) -> list[Segment]:
    """Split each segment where its own words change speaker. A segment with no
    word timings cannot be split and is returned as it is."""
    out: list[Segment] = []
    for segment in segments:
        words = segment.words or []
        if len(words) < 2:
            out.append(segment)
            continue
        runs = _speaker_runs(words, speaker_segments)
        if len(runs) < 2:
            out.append(segment)
            continue
        for _speaker, run_words in runs:
            out.append(
                Segment(
                    text=" ".join(w.text for w in run_words),
                    start=run_words[0].start,
                    end=run_words[-1].end,
                    words=list(run_words),
                )
            )
    return out


def align(
    segments: list[Segment],
    speaker_segments: list[SpeakerSegment],
    keep_words: bool = False,
) -> list[Turn]:
    """Align transcription segments to speakers and group into turns.

    A segment is split where its words change speaker, then each piece is split
    into sentences and assigned to the speaker covering the majority of its
    duration. Consecutive sentences from the same speaker are grouped into
    turns. ``keep_words`` retains per-word timings on each sentence for
    word-level/v2 output.
    """
    if not segments or not speaker_segments:
        return []

    segments = split_on_speaker_change(segments, speaker_segments)

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
