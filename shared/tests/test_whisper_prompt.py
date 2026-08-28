"""Loading the Whisper custom-vocabulary prompt.

Two load-bearing properties: comments are stripped (so what reaches the model is
exactly the term text, never the reviewer's notes), and what the loader returns is
never truncated (so a reviewer reading the file sees exactly what Whisper is fed -
an over-budget file warns rather than silently dropping terms)."""

from whisper_prompt import load_prompt


def test_bundled_prompt_loads_and_strips_comments():
    p = load_prompt()
    assert p
    assert "#" not in p, "comment lines must not reach the model"
    assert "UAP" in p and "David Grusch" in p and "Roswell" in p


def test_disabled_by_env(monkeypatch):
    monkeypatch.setenv("INGEST_WHISPER_PROMPT", "0")
    assert load_prompt() is None


def test_missing_file_is_none(tmp_path):
    assert load_prompt(tmp_path / "nope.txt") is None


def test_custom_file_strips_comments_and_joins(tmp_path):
    f = tmp_path / "p.txt"
    f.write_text("# a comment\nUFO, UAP\n\n#another\nRoswell\n")
    assert load_prompt(f) == "UFO, UAP Roswell"


def test_over_budget_warns_but_does_not_truncate(tmp_path, capsys):
    f = tmp_path / "big.txt"
    f.write_text(", ".join(["word"] * 300))
    out = load_prompt(f)
    assert out.count("word") == 300, "must not truncate what the reviewer wrote"
    assert "WARNING" in capsys.readouterr().err
