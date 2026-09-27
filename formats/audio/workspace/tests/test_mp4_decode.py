"""The diarisation input must decode an acquired MP4/AAC without modifying it."""

import subprocess

from diarisation.pyannote_diarise import _load_audio


def test_mp4_aac_decodes_for_diarisation(tmp_path):
    video = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:a",
            "aac",
            str(video),
        ],
        check=True,
    )
    original = video.read_bytes()
    waveform, rate = _load_audio(video)
    assert rate == 16000
    assert waveform.shape[0] == 1
    assert 15000 <= waveform.shape[1] <= 16500  # AAC encoder padding
    assert video.read_bytes() == original
