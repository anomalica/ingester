from alignment.align import align
from models import Segment, SpeakerSegment, Word


def _seg(text, start, end, words):
    """Shorthand to build a Segment from (text, start, end) word tuples."""
    return Segment(
        text=text,
        start=start,
        end=end,
        words=[Word(text=t, start=s, end=e) for t, s, e in words],
    )


def test_two_speakers_basic():
    segments = [
        _seg("Hello world.", 0.0, 2.0, [("Hello", 0.0, 0.5), ("world.", 0.6, 1.0)]),
        _seg(
            "How are you?",
            2.5,
            4.0,
            [("How", 2.5, 2.8), ("are", 2.9, 3.1), ("you?", 3.2, 3.5)],
        ),
    ]
    speaker_segments = [
        SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=2.0),
        SpeakerSegment(speaker="SPEAKER_01", start=2.5, end=4.0),
    ]

    turns = align(segments, speaker_segments)

    assert len(turns) == 2
    assert turns[0].speaker == "SPEAKER_00"
    assert len(turns[0].sentences) == 1
    assert turns[0].sentences[0].text == "Hello world."
    assert turns[0].sentences[0].time == 0.0
    assert turns[1].speaker == "SPEAKER_01"
    assert turns[1].sentences[0].text == "How are you?"
    assert turns[1].sentences[0].time == 2.5


def test_single_speaker_groups_segments():
    segments = [
        _seg("Hello world.", 0.0, 2.0, [("Hello", 0.0, 0.5), ("world.", 0.6, 1.0)]),
        _seg("More text.", 2.5, 4.0, [("More", 2.5, 2.8), ("text.", 2.9, 3.5)]),
    ]
    speaker_segments = [
        SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=4.0),
    ]

    turns = align(segments, speaker_segments)

    assert len(turns) == 1
    assert turns[0].speaker == "SPEAKER_00"
    assert len(turns[0].sentences) == 2
    assert turns[0].sentences[0].text == "Hello world."
    assert turns[0].sentences[1].text == "More text."
    assert turns[0].sentences[1].time == 2.5


def test_segment_kept_whole_despite_diarisation_boundary():
    """A transcription segment should stay with the majority speaker even if
    diarisation puts a boundary partway through it."""
    segments = [
        _seg(
            "So what you're telling me is that UFOs are real.",
            44.0,
            53.0,
            [
                ("So", 44.0, 44.2),
                ("what", 44.3, 44.5),
                ("you're", 44.6, 44.9),
                ("telling", 45.0, 45.3),
                ("me", 45.4, 45.5),
                ("is", 45.6, 45.7),
                ("that", 45.8, 46.0),
                ("UFOs", 46.1, 46.5),
                ("are", 52.0, 52.3),
                ("real.", 52.5, 53.0),
            ],
        ),
    ]
    speaker_segments = [
        SpeakerSegment(speaker="SPEAKER_00", start=40.0, end=51.0),
        SpeakerSegment(speaker="SPEAKER_01", start=51.0, end=60.0),
    ]

    turns = align(segments, speaker_segments)

    assert len(turns) == 1
    assert turns[0].speaker == "SPEAKER_00"
    assert "are real." in turns[0].sentences[0].text


def test_preserves_segment_text_with_punctuation():
    segments = [
        _seg(
            "The Advanced Aerospace Threat Identification Program, or AATIP.",
            75.0,
            84.0,
            [("The", 75.0, 75.2), ("AATIP.", 83.5, 84.0)],
        ),
    ]
    speaker_segments = [
        SpeakerSegment(speaker="SPEAKER_00", start=70.0, end=90.0),
    ]

    turns = align(segments, speaker_segments)

    assert "or AATIP." in turns[0].sentences[0].text


def test_empty_segments():
    turns = align([], [SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=5.0)])
    assert turns == []


def test_empty_speaker_segments():
    segments = [
        _seg("Hello.", 0.0, 1.0, [("Hello.", 0.0, 0.5)]),
    ]
    turns = align(segments, [])
    assert turns == []


def test_segment_in_gap_assigned_to_nearest():
    segments = [
        _seg("Gap text.", 2.2, 2.5, [("Gap", 2.2, 2.3), ("text.", 2.3, 2.5)]),
    ]
    speaker_segments = [
        SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=2.0),
        SpeakerSegment(speaker="SPEAKER_01", start=3.0, end=5.0),
    ]

    turns = align(segments, speaker_segments)

    assert len(turns) == 1
    assert turns[0].speaker == "SPEAKER_00"


def test_empty_text_segments_skipped():
    segments = [
        Segment(text="", start=0.0, end=1.0, words=[]),
        _seg("Hello.", 2.0, 3.0, [("Hello.", 2.0, 2.5)]),
    ]
    speaker_segments = [
        SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=5.0),
    ]

    turns = align(segments, speaker_segments)

    assert len(turns) == 1
    assert turns[0].sentences[0].text == "Hello."


def test_sentence_time_is_segment_start():
    segments = [
        _seg("A B.", 1.5, 3.0, [("A", 1.5, 1.8), ("B.", 2.0, 2.5)]),
    ]
    speaker_segments = [
        SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=5.0),
    ]

    turns = align(segments, speaker_segments)

    assert turns[0].sentences[0].time == 1.5


def test_multiple_sentences_per_turn():
    """Multiple transcription segments from the same speaker become
    individual timed sentences within a single turn."""
    segments = [
        _seg(
            "First sentence.", 0.0, 2.0, [("First", 0.0, 0.5), ("sentence.", 0.6, 1.0)]
        ),
        _seg(
            "Second sentence.",
            2.5,
            4.0,
            [("Second", 2.5, 2.8), ("sentence.", 2.9, 3.5)],
        ),
        _seg(
            "Third sentence.",
            4.5,
            6.0,
            [("Third", 4.5, 4.8), ("sentence.", 4.9, 5.5)],
        ),
    ]
    speaker_segments = [
        SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=6.0),
    ]

    turns = align(segments, speaker_segments)

    assert len(turns) == 1
    assert len(turns[0].sentences) == 3
    assert turns[0].sentences[0].text == "First sentence."
    assert turns[0].sentences[0].time == 0.0
    assert turns[0].sentences[1].text == "Second sentence."
    assert turns[0].sentences[1].time == 2.5
    assert turns[0].sentences[2].text == "Third sentence."
    assert turns[0].sentences[2].time == 4.5


def test_multi_sentence_segment_split():
    """A single WhisperX segment containing multiple sentences should be
    split into individual timed sentences using word timestamps."""
    segments = [
        _seg(
            "First sentence. Second sentence. Third sentence.",
            0.0,
            6.0,
            [
                ("First", 0.0, 0.3),
                ("sentence.", 0.4, 0.8),
                ("Second", 2.0, 2.3),
                ("sentence.", 2.4, 2.8),
                ("Third", 4.0, 4.3),
                ("sentence.", 4.4, 4.8),
            ],
        ),
    ]
    speaker_segments = [
        SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=6.0),
    ]

    turns = align(segments, speaker_segments)

    assert len(turns) == 1
    assert len(turns[0].sentences) == 3
    assert turns[0].sentences[0].text == "First sentence."
    assert turns[0].sentences[0].time == 0.0
    assert turns[0].sentences[1].text == "Second sentence."
    assert turns[0].sentences[1].time == 2.0
    assert turns[0].sentences[2].text == "Third sentence."
    assert turns[0].sentences[2].time == 4.0


def test_abbreviation_does_not_break_line():
    """An honorific like 'Dr.' must not strand the abbreviation on its own
    line - the sentence continues past it."""
    segments = [
        _seg(
            "Dr. Smith was there. He left.",
            0.0,
            4.0,
            [
                ("Dr.", 0.0, 0.3),
                ("Smith", 0.4, 0.7),
                ("was", 0.8, 1.0),
                ("there.", 1.1, 1.5),
                ("He", 2.0, 2.2),
                ("left.", 2.3, 2.6),
            ],
        ),
    ]
    speaker_segments = [SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=4.0)]

    turns = align(segments, speaker_segments)

    sentences = turns[0].sentences
    assert [s.text for s in sentences] == ["Dr. Smith was there.", "He left."]


def test_single_letter_initials_not_split():
    segments = [
        _seg(
            "George W. Bush spoke today.",
            0.0,
            3.0,
            [
                ("George", 0.0, 0.3),
                ("W.", 0.4, 0.6),
                ("Bush", 0.7, 1.0),
                ("spoke", 1.1, 1.4),
                ("today.", 1.5, 1.8),
            ],
        ),
    ]
    speaker_segments = [SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=3.0)]

    turns = align(segments, speaker_segments)

    assert len(turns[0].sentences) == 1
    assert turns[0].sentences[0].text == "George W. Bush spoke today."


def test_dotted_acronym_not_split():
    segments = [
        _seg(
            "The U.S. government denied it.",
            0.0,
            3.0,
            [
                ("The", 0.0, 0.2),
                ("U.S.", 0.3, 0.6),
                ("government", 0.7, 1.2),
                ("denied", 1.3, 1.6),
                ("it.", 1.7, 1.9),
            ],
        ),
    ]
    speaker_segments = [SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=3.0)]

    turns = align(segments, speaker_segments)

    assert len(turns[0].sentences) == 1
    assert turns[0].sentences[0].text == "The U.S. government denied it."


def test_real_sentences_still_split_after_abbreviation_handling():
    """Regression: genuine sentence boundaries must still split."""
    segments = [
        _seg(
            "He arrived. She waved. They left.",
            0.0,
            6.0,
            [
                ("He", 0.0, 0.3),
                ("arrived.", 0.4, 0.8),
                ("She", 2.0, 2.3),
                ("waved.", 2.4, 2.8),
                ("They", 4.0, 4.3),
                ("left.", 4.4, 4.8),
            ],
        ),
    ]
    speaker_segments = [SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=6.0)]

    turns = align(segments, speaker_segments)

    assert [s.text for s in turns[0].sentences] == [
        "He arrived.",
        "She waved.",
        "They left.",
    ]


def test_single_sentence_segment_not_split():
    """A segment with one sentence should not be split."""
    segments = [
        _seg(
            "Just one sentence here.",
            0.0,
            2.0,
            [
                ("Just", 0.0, 0.2),
                ("one", 0.3, 0.5),
                ("sentence", 0.6, 1.0),
                ("here.", 1.1, 1.5),
            ],
        ),
    ]
    speaker_segments = [
        SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=2.0),
    ]

    turns = align(segments, speaker_segments)

    assert len(turns) == 1
    assert len(turns[0].sentences) == 1
    assert turns[0].sentences[0].text == "Just one sentence here."
