from pathlib import Path

from benchmark_exclusive_diarisation import _prepared_audio, aggregate


def _result(wrong_words, wrong_turns):
    return {
        "matched_words": 100,
        "wrong_words": wrong_words,
        "word_error_pct": float(wrong_words),
        "turns": 20,
        "wrong_turns": wrong_turns,
        "turn_error_pct": 5.0 * wrong_turns,
        "labels": 2,
    }


def test_adoption_requires_fewer_word_errors_without_more_wrong_turns():
    passing = {
        "a": {"regular": _result(10, 2), "exclusive": _result(8, 2)},
        "b": {"regular": _result(5, 1), "exclusive": _result(4, 1)},
    }
    adopted = aggregate(passing)
    assert adopted["adopt_exclusive"] is True
    assert adopted["production_decision"]["code"] == "adopt-exclusive"

    passing["b"]["exclusive"] = _result(3, 2)
    retained = aggregate(passing)
    assert retained["adopt_exclusive"] is False
    assert retained["production_decision"] == {
        "code": "retain-regular",
        "summary": "Retain regular Community-1 tracks for speaker attribution.",
    }


def test_prepared_audio_is_16khz_mono(tmp_path: Path):
    import subprocess
    import wave

    source = tmp_path / "source.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo",
            "-t",
            "0.1",
            str(source),
        ],
        check=True,
    )

    with _prepared_audio(source) as prepared:
        with wave.open(str(prepared)) as audio:
            assert audio.getframerate() == 16000
            assert audio.getnchannels() == 1
            assert audio.getsampwidth() == 2

    assert not prepared.exists()
