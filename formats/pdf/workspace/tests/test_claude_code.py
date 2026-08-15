"""Tests for Claude Code provider response parsing and error handling."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from extraction.claude_code import ClaudeCodeProvider, _extract_metadata


# --- Metadata extraction ---


def test_extract_metadata_full_envelope():
    envelope = {
        "duration_ms": 5000,
        "total_cost_usd": 0.15,
        "modelUsage": {"claude-sonnet-4-6": {"inputTokens": 100}},
        "usage": {
            "input_tokens": 100,
            "output_tokens": 200,
            "cache_read_input_tokens": 50,
            "cache_creation_input_tokens": 10,
        },
        "num_turns": 3,
    }
    meta = _extract_metadata(envelope)
    assert meta["duration_ms"] == 5000
    assert meta["cost_usd"] == 0.15
    assert meta["num_turns"] == 3
    assert meta["tokens"]["input_tokens"] == 100
    assert meta["tokens"]["output_tokens"] == 200


def test_extract_metadata_minimal_envelope():
    meta = _extract_metadata({})
    assert meta == {}


def test_extract_metadata_partial_usage():
    envelope = {"usage": {"input_tokens": 50, "output_tokens": 100}}
    meta = _extract_metadata(envelope)
    assert meta["tokens"]["input_tokens"] == 50
    assert "cache_read_input_tokens" not in meta["tokens"]


# --- Response parsing ---


def _mock_subprocess_result(result_text, cost=0.1, turns=2):
    """Build a mock subprocess.CompletedProcess with a Claude Code envelope."""
    envelope = {
        "type": "result",
        "subtype": "success",
        "result": result_text,
        "duration_ms": 1000,
        "total_cost_usd": cost,
        "num_turns": turns,
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = json.dumps(envelope)
    mock.stderr = ""
    return mock


def test_extract_returns_content_and_metadata():
    provider = ClaudeCodeProvider()
    mock_result = _mock_subprocess_result(
        "---\nschema: anomalica/record/1\n---\n\nHello."
    )
    with patch("extraction.claude_code.subprocess.run", return_value=mock_result):
        content, meta = provider.extract(Path("/tmp/test.pdf"))
    assert "Hello." in content
    assert meta["cost_usd"] == 0.1


def test_extract_strips_markdown_code_fences():
    provider = ClaudeCodeProvider()
    wrapped = "```markdown\n---\nschema: anomalica/record/1\n---\n\nContent here.\n```"
    mock_result = _mock_subprocess_result(wrapped)
    with patch("extraction.claude_code.subprocess.run", return_value=mock_result):
        content, meta = provider.extract(Path("/tmp/test.pdf"))
    assert not content.startswith("```")
    assert not content.endswith("```")
    assert "Content here." in content


def test_extract_strips_code_fence_with_no_language():
    provider = ClaudeCodeProvider()
    wrapped = "```\n---\nschema: anomalica/record/1\n---\n\nContent.\n```"
    mock_result = _mock_subprocess_result(wrapped)
    with patch("extraction.claude_code.subprocess.run", return_value=mock_result):
        content, meta = provider.extract(Path("/tmp/test.pdf"))
    assert not content.startswith("```")
    assert "Content." in content


def test_extract_leaves_content_without_fences_unchanged():
    provider = ClaudeCodeProvider()
    raw = "---\nschema: anomalica/record/1\n---\n\nNo fences here."
    mock_result = _mock_subprocess_result(raw)
    with patch("extraction.claude_code.subprocess.run", return_value=mock_result):
        content, meta = provider.extract(Path("/tmp/test.pdf"))
    assert content == raw


# --- Error handling ---


def test_extract_raises_on_nonzero_exit():
    provider = ClaudeCodeProvider()
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "some error"
    with patch("extraction.claude_code.subprocess.run", return_value=mock_result):
        with pytest.raises(RuntimeError, match="Claude Code failed"):
            provider.extract(Path("/tmp/test.pdf"))


def test_extract_raises_on_empty_stdout():
    provider = ClaudeCodeProvider()
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = ""
    mock_result.stderr = ""
    with patch("extraction.claude_code.subprocess.run", return_value=mock_result):
        with pytest.raises(RuntimeError, match="empty response"):
            provider.extract(Path("/tmp/test.pdf"))


def test_extract_raises_on_empty_result_field():
    provider = ClaudeCodeProvider()
    envelope = {"type": "result", "result": "", "usage": {}}
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = json.dumps(envelope)
    mock_result.stderr = ""
    with patch("extraction.claude_code.subprocess.run", return_value=mock_result):
        with pytest.raises(RuntimeError, match="empty result"):
            provider.extract(Path("/tmp/test.pdf"))


# --- Chunk extraction ---


def test_extract_chunk_writes_temp_file_and_cleans_up(tmp_path):
    provider = ClaudeCodeProvider()
    mock_result = _mock_subprocess_result(
        "---\nschema: anomalica/record/1\n---\n\nChunk content."
    )

    pdf_bytes = b"%PDF-1.4 fake content"
    temp_files_created = []

    def capture_run(*args, **kwargs):
        # The prompt contains the temp file path - capture it
        prompt = kwargs.get("input", "")
        for word in prompt.split():
            if word.endswith(".pdf"):
                temp_files_created.append(Path(word))
        return mock_result

    with patch("extraction.claude_code.subprocess.run", side_effect=capture_run):
        content, meta = provider.extract_chunk(pdf_bytes, page_offset=1, page_count=10)

    assert "Chunk content." in content
    # Temp file should have been cleaned up
    for p in temp_files_created:
        assert not p.exists(), f"Temp file {p} was not cleaned up"


def test_the_api_key_is_hidden_from_claude_code(monkeypatch, tmp_path):
    """ANTHROPIC_API_KEY takes precedence over the claude.ai login, which disables
    the connectors and makes the CLI exit 1 with a warning instead of extracting.

    This path IS the subscription path - the API path never reaches here - so the
    key is not merely unnecessary, it actively breaks the run. It cost intermittent
    chunk failures on a 416-page document, each one burning a retry.
    """
    import subprocess as sp

    from extraction import claude_code as cc  # noqa: F401

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-be-passed")
    monkeypatch.setenv("KEEP_ME", "yes")

    seen: dict = {}

    def _fake_run(cmd, **kw):
        seen.update(kw)
        return sp.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr(cc.subprocess, "run", _fake_run)

    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    try:
        cc.ClaudeCodeProvider("claude-opus-5")._call_claude("prompt", pdf)
    except Exception:
        pass  # the call fails by design here; the environment is what matters

    assert seen, "subprocess.run was never reached - the test proves nothing"

    env = seen.get("env") or {}
    assert "ANTHROPIC_API_KEY" not in env
    assert env.get("KEEP_ME") == "yes"  # the rest of the environment survives
