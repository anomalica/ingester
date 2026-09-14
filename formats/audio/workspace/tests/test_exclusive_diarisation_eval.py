from pathlib import Path

from evaluate_exclusive_diarisation import evaluate


FIXTURE = Path(__file__).parent / "fixtures" / "community1-exclusive.json"


def test_exclusive_tracks_are_scored_through_production_alignment():
    result = evaluate(FIXTURE)

    assert result["regular"] == {
        "matched_words": 12,
        "wrong_words": 6,
        "word_error_pct": 50.0,
        "turns": 1,
        "wrong_turns": 0,
        "turn_error_pct": 0.0,
        "labels": 1,
    }
    assert result["exclusive"] == {
        "matched_words": 12,
        "wrong_words": 0,
        "word_error_pct": 0.0,
        "turns": 2,
        "wrong_turns": 0,
        "turn_error_pct": 0.0,
        "labels": 2,
    }
