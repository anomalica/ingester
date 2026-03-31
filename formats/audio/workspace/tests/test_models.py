from models import (
    Word,
    Segment,
    SpeakerSegment,
    Turn,
    format_time,
    detect_source_type,
)


def test_word_construction():
    w = Word(text="hello", start=0.0, end=0.5)
    assert w.text == "hello"
    assert w.start == 0.0
    assert w.end == 0.5


def test_segment_construction():
    words = [Word(text="hello", start=0.0, end=0.5)]
    seg = Segment(text="hello", start=0.0, end=0.5, words=words)
    assert seg.text == "hello"
    assert len(seg.words) == 1


def test_speaker_segment_construction():
    ss = SpeakerSegment(speaker="SPEAKER_00", start=0.0, end=5.0)
    assert ss.speaker == "SPEAKER_00"


def test_turn_construction():
    t = Turn(speaker="SPEAKER_00", time=1.5, text="Hello world")
    assert t.speaker == "SPEAKER_00"
    assert t.time == 1.5
    assert t.text == "Hello world"


def test_format_time_zero():
    assert format_time(0.0) == "00:00:00"


def test_format_time_seconds_only():
    assert format_time(45.0) == "00:00:45"


def test_format_time_minutes_and_seconds():
    assert format_time(83.0) == "00:01:23"


def test_format_time_hours():
    assert format_time(7200.0) == "02:00:00"


def test_format_time_mixed():
    assert format_time(3723.0) == "01:02:03"


def test_format_time_fractional_truncates():
    assert format_time(83.7) == "00:01:23"


def test_detect_source_type_audio_mpeg():
    assert detect_source_type("audio/mpeg") == "audio"


def test_detect_source_type_audio_wav():
    assert detect_source_type("audio/wav") == "audio"


def test_detect_source_type_video_mp4():
    assert detect_source_type("video/mp4") == "video"


def test_detect_source_type_video_webm():
    assert detect_source_type("video/webm") == "video"
