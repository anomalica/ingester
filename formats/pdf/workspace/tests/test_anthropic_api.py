"""Tests for Anthropic API provider response parsing and error handling."""

from unittest.mock import MagicMock, patch

import pytest

from extraction.anthropic_api import AnthropicProvider, ContentFilteredError


def _mock_message(text, input_tokens=100, output_tokens=50, **extra_usage):
    """Build a mock Anthropic message response."""
    msg = MagicMock()
    content_block = MagicMock()
    content_block.text = text
    msg.content = [content_block]
    msg.usage = MagicMock()
    msg.usage.input_tokens = input_tokens
    msg.usage.output_tokens = output_tokens
    for k, v in extra_usage.items():
        setattr(msg.usage, k, v)
    return msg


def _mock_stream(message):
    """Build a mock streaming context manager that returns a final message."""
    stream = MagicMock()
    stream.__enter__ = MagicMock(return_value=stream)
    stream.__exit__ = MagicMock(return_value=False)
    stream.get_final_message.return_value = message
    return stream


@pytest.fixture()
def pdf_file(tmp_path):
    """Create a minimal PDF file for testing."""
    f = tmp_path / "test.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    return f


def test_extract_returns_content_and_metadata(pdf_file):
    provider = AnthropicProvider()
    msg = _mock_message("---\nschema: anomalica/record/1\n---\n\nHello.")
    stream = _mock_stream(msg)
    with patch.object(provider.client.messages, "stream", return_value=stream):
        content, meta = provider.extract(pdf_file)
    assert "Hello." in content
    assert meta["input_tokens"] == 100
    assert meta["output_tokens"] == 50


def test_extract_strips_code_fences(pdf_file):
    provider = AnthropicProvider()
    msg = _mock_message("```markdown\n---\nschema: test\n---\n\nContent.\n```")
    stream = _mock_stream(msg)
    with patch.object(provider.client.messages, "stream", return_value=stream):
        content, meta = provider.extract(pdf_file)
    assert not content.startswith("```")
    assert not content.endswith("```")
    assert "Content." in content


def test_extract_without_fences_unchanged(pdf_file):
    provider = AnthropicProvider()
    raw = "---\nschema: test\n---\n\nNo fences."
    msg = _mock_message(raw)
    stream = _mock_stream(msg)
    with patch.object(provider.client.messages, "stream", return_value=stream):
        content, meta = provider.extract(pdf_file)
    assert content == raw


def test_content_filter_raises_content_filtered_error(pdf_file):
    import anthropic

    provider = AnthropicProvider()
    error = anthropic.BadRequestError(
        message="Output blocked by content filtering policy",
        response=MagicMock(status_code=400),
        body={"error": {"message": "Output blocked by content filtering policy"}},
    )
    with patch.object(provider.client.messages, "stream", side_effect=error):
        with pytest.raises(ContentFilteredError):
            provider.extract(pdf_file)


def test_other_bad_request_raises_runtime_error(pdf_file):
    import anthropic

    provider = AnthropicProvider()
    error = anthropic.BadRequestError(
        message="Some other error",
        response=MagicMock(status_code=400),
        body={"error": {"message": "Some other error"}},
    )
    with patch.object(provider.client.messages, "stream", side_effect=error):
        with pytest.raises(RuntimeError, match="Some other error"):
            provider.extract(pdf_file)


def test_cache_tokens_included_when_present(pdf_file):
    provider = AnthropicProvider()
    msg = _mock_message(
        "---\nschema: test\n---\n\nCached.",
        cache_creation_input_tokens=500,
        cache_read_input_tokens=200,
    )
    stream = _mock_stream(msg)
    with patch.object(provider.client.messages, "stream", return_value=stream):
        content, meta = provider.extract(pdf_file)
    assert meta["cache_creation_input_tokens"] == 500
    assert meta["cache_read_input_tokens"] == 200


def test_extract_chunk_passes_page_offset():
    provider = AnthropicProvider()
    msg = _mock_message("---\nschema: test\n---\n\nChunk.")
    stream = _mock_stream(msg)
    with patch.object(
        provider.client.messages, "stream", return_value=stream
    ) as mock_stream:
        content, meta = provider.extract_chunk(b"%PDF", page_offset=21, page_count=20)
    assert "Chunk." in content
    # Verify the prompt included the page offset
    call_kwargs = mock_stream.call_args[1]
    prompt_text = call_kwargs["messages"][0]["content"][1]["text"]
    assert "21" in prompt_text
    assert "40" in prompt_text
