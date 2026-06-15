from models import Segment, SpeakerSegment, Word
from transcript_cache import archive_path, load_raw_archive, save_raw_archive


def test_archive_round_trip_reconstructs_segments(tmp_path):
    """The raw archive stores whisperx+pyannote verbatim, and load reconstructs
    the processed segments/speakers for a no-GPU re-render."""
    whisperx_raw = {
        "language": "en",
        "transcribe": {"language": "en", "segments": []},
        "aligned": {
            "segments": [
                {
                    "text": "Hello there",
                    "start": 0.0,
                    "end": 1.2,
                    "words": [
                        {"word": "Hello", "start": 0.0, "end": 0.5, "score": 0.9},
                        {"word": "there", "start": 0.6, "end": 1.2, "score": 0.8},
                    ],
                }
            ]
        },
    }
    pyannote_raw = {
        "model": "test",
        "tracks": [{"start": 0.0, "end": 1.2, "speaker": "SPEAKER_00", "track": "A"}],
    }

    path = archive_path(tmp_path, "abc123")
    save_raw_archive(path, whisperx_raw, pyannote_raw, meta={"whisper_model": "x"})

    # the complete raw is preserved verbatim (confidence scores survive)
    import json

    stored = json.loads(path.read_text())
    assert stored["whisperx"]["aligned"]["segments"][0]["words"][0]["score"] == 0.9
    assert path.name == "abc123.transcript.json"

    segments, speakers = load_raw_archive(path)
    assert segments == [
        Segment(
            text="Hello there",
            start=0.0,
            end=1.2,
            words=[Word("Hello", 0.0, 0.5), Word("there", 0.6, 1.2)],
        )
    ]
    assert speakers == [SpeakerSegment("SPEAKER_00", 0.0, 1.2)]
