from pathlib import Path

from benchmark_exclusive_diarisation import _prepared_audio, _record_path, aggregate


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


def test_record_path_resolves_record3_selection_identity(tmp_path: Path):
    asset_hash = "a" * 64
    record_hash = "b" * 64
    record = tmp_path / f"{record_hash}.md"
    record.write_text(
        "---\n"
        "schema: anomalica/record/3\n"
        f"content_hash: sha256:{record_hash}\n"
        "assets:\n"
        f"- asset_hash: sha256:{asset_hash}\n"
        "selection:\n"
        f"- asset_hash: sha256:{asset_hash}\n"
        "  selector:\n"
        "    type: whole\n"
        "---\nbody\n"
    )

    assert _record_path(tmp_path, asset_hash) == record


def test_record_path_preserves_legacy_record2_preference(tmp_path: Path):
    asset_hash = "c" * 64
    legacy = tmp_path / f"{asset_hash}.md"
    word_timed = tmp_path / f"{asset_hash}.v2.md"
    legacy.write_text("legacy")
    word_timed.write_text("word timed")

    assert _record_path(tmp_path, asset_hash) == word_timed
