from dataclasses import dataclass

from diarisation.pyannote_diarise import DIARISATION_MODEL, _serialise_result
from models import SpeakerSegment


@dataclass
class _Turn:
    start: float
    end: float


class _Annotation:
    def __init__(self, tracks):
        self.tracks = tracks

    def itertracks(self, yield_label=False):
        assert yield_label
        yield from self.tracks


def test_community1_exclusive_tracks_are_archived_but_not_selected_by_default():
    regular = _Annotation(
        [
            (_Turn(0.0, 2.0), "A", "SPEAKER_00"),
            (_Turn(1.0, 2.0), "B", "SPEAKER_01"),
        ]
    )
    exclusive = _Annotation(
        [
            (_Turn(0.0, 1.0), "A", "SPEAKER_00"),
            (_Turn(1.0, 2.0), "B", "SPEAKER_01"),
        ]
    )
    result = type(
        "Result",
        (),
        {
            "speaker_diarization": regular,
            "exclusive_speaker_diarization": exclusive,
        },
    )()

    segments, raw = _serialise_result(result)

    assert segments == [
        SpeakerSegment("SPEAKER_00", 0.0, 2.0),
        SpeakerSegment("SPEAKER_01", 1.0, 2.0),
    ]
    assert raw["model"] == DIARISATION_MODEL
    assert [track["start"] for track in raw["tracks"]] == [0.0, 1.0]
    assert [track["start"] for track in raw["exclusive_tracks"]] == [0.0, 1.0]
