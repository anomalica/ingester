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
    assert turns[0].text == "Hello world."
    assert turns[0].time == 0.0
    assert turns[1].speaker == "SPEAKER_01"
    assert turns[1].text == "How are you?"
    assert turns[1].time == 2.5


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
    assert turns[0].text == "Hello world.\nMore text."


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
    # Diarisation says SPEAKER_00 ends at 51.0 and SPEAKER_01 starts at 51.0
    # but the segment runs 44.0-53.0, so SPEAKER_00 covers the majority (7s vs 2s)
    speaker_segments = [
        SpeakerSegment(speaker="SPEAKER_00", start=40.0, end=51.0),
        SpeakerSegment(speaker="SPEAKER_01", start=51.0, end=60.0),
    ]

    turns = align(segments, speaker_segments)

    assert len(turns) == 1
    assert turns[0].speaker == "SPEAKER_00"
    assert "are real." in turns[0].text


def test_preserves_segment_text_with_punctuation():
    """Segments should preserve their original text including punctuation."""
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

    assert "or AATIP." in turns[0].text


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
    """Segments falling between speaker segments go to the nearest one."""
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
    assert turns[0].text == "Hello."


def test_turn_time_is_first_segment_start():
    segments = [
        _seg("A B.", 1.5, 3.0, [("A", 1.5, 1.8), ("B.", 2.0, 2.5)]),
    ]
    speaker_segments = [
        SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=5.0),
    ]

    turns = align(segments, speaker_segments)

    assert turns[0].time == 1.5


def test_multiple_segments_per_turn_separated_by_newlines():
    """Multiple transcription segments from the same speaker should be
    separated by newlines to preserve natural paragraph breaks."""
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
    assert turns[0].text == "First sentence.\nSecond sentence.\nThird sentence."
