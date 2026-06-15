from models import Segment, SpeakerSegment, Word
from transcript_cache import (
    cache_path,
    load_transcript_cache,
    save_transcript_cache,
)


def test_round_trip_preserves_segments_and_speakers(tmp_path):
    segments = [
        Segment(
            text="Hello there",
            start=0.0,
            end=1.2,
            words=[Word("Hello", 0.0, 0.5), Word("there", 0.6, 1.2)],
        ),
    ]
    speakers = [SpeakerSegment("SPEAKER_00", 0.0, 1.2)]
    path = cache_path(tmp_path, "abc123")
    save_transcript_cache(path, segments, speakers, meta={"whisper_model": "x"})

    loaded_segs, loaded_speakers = load_transcript_cache(path)
    assert loaded_segs == segments
    assert loaded_speakers == speakers
    # the cache is named as a sidecar of the record hash
    assert path.name == "abc123.transcript.json"
