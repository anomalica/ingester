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
        _seg("Hello world", 0.0, 2.0, [("Hello", 0.0, 0.5), ("world", 0.6, 1.0)]),
        _seg(
            "How are you",
            2.5,
            4.0,
            [("How", 2.5, 2.8), ("are", 2.9, 3.1), ("you", 3.2, 3.5)],
        ),
    ]
    speaker_segments = [
        SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=2.0),
        SpeakerSegment(speaker="SPEAKER_01", start=2.5, end=4.0),
    ]

    turns = align(segments, speaker_segments)

    assert len(turns) == 2
    assert turns[0].speaker == "SPEAKER_00"
    assert turns[0].text == "Hello world"
    assert turns[0].time == 0.0
    assert turns[1].speaker == "SPEAKER_01"
    assert turns[1].text == "How are you"
    assert turns[1].time == 2.5


def test_single_speaker():
    segments = [
        _seg("Hello world", 0.0, 2.0, [("Hello", 0.0, 0.5), ("world", 0.6, 1.0)]),
        _seg("More text", 2.5, 4.0, [("More", 2.5, 2.8), ("text", 2.9, 3.5)]),
    ]
    speaker_segments = [
        SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=4.0),
    ]

    turns = align(segments, speaker_segments)

    assert len(turns) == 1
    assert turns[0].speaker == "SPEAKER_00"
    assert turns[0].text == "Hello world More text"


def test_speaker_change_mid_segment():
    segments = [
        _seg(
            "Hello world how are you",
            0.0,
            5.0,
            [
                ("Hello", 0.0, 0.5),
                ("world", 0.6, 1.0),
                ("how", 3.0, 3.3),
                ("are", 3.4, 3.6),
                ("you", 3.7, 4.0),
            ],
        ),
    ]
    speaker_segments = [
        SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=2.0),
        SpeakerSegment(speaker="SPEAKER_01", start=2.5, end=5.0),
    ]

    turns = align(segments, speaker_segments)

    assert len(turns) == 2
    assert turns[0].speaker == "SPEAKER_00"
    assert turns[0].text == "Hello world"
    assert turns[1].speaker == "SPEAKER_01"
    assert turns[1].text == "how are you"


def test_word_in_gap_assigned_to_nearest():
    """Words falling between speaker segments should be assigned to the nearest one."""
    segments = [
        _seg("gap word", 2.2, 2.5, [("gap", 2.2, 2.3), ("word", 2.3, 2.5)]),
    ]
    speaker_segments = [
        SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=2.0),
        SpeakerSegment(speaker="SPEAKER_01", start=3.0, end=5.0),
    ]

    turns = align(segments, speaker_segments)

    assert len(turns) == 1
    # Midpoint of "gap" is 2.25, closer to SPEAKER_00 ending at 2.0 than SPEAKER_01 starting at 3.0
    assert turns[0].speaker == "SPEAKER_00"


def test_empty_segments():
    turns = align([], [SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=5.0)])
    assert turns == []


def test_empty_speaker_segments():
    segments = [
        _seg("Hello", 0.0, 1.0, [("Hello", 0.0, 0.5)]),
    ]
    turns = align(segments, [])
    assert turns == []


def test_segment_with_no_words_skipped():
    segments = [
        Segment(text="empty", start=0.0, end=1.0, words=[]),
        _seg("Hello", 2.0, 3.0, [("Hello", 2.0, 2.5)]),
    ]
    speaker_segments = [
        SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=5.0),
    ]

    turns = align(segments, speaker_segments)

    assert len(turns) == 1
    assert turns[0].text == "Hello"


def test_turn_time_is_first_word_start():
    segments = [
        _seg("A B", 1.0, 3.0, [("A", 1.5, 1.8), ("B", 2.0, 2.5)]),
    ]
    speaker_segments = [
        SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=5.0),
    ]

    turns = align(segments, speaker_segments)

    assert turns[0].time == 1.5
