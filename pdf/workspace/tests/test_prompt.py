from extraction.prompt import build_extraction_prompt


def test_prompt_contains_schema():
    prompt = build_extraction_prompt()
    assert '"type"' in prompt
    assert '"page"' in prompt
    assert "metadata" in prompt


def test_prompt_mentions_redaction():
    prompt = build_extraction_prompt()
    assert "[REDACTED]" in prompt


def test_prompt_mentions_image_description():
    prompt = build_extraction_prompt()
    assert "image" in prompt.lower()
    assert "description" in prompt.lower()


def test_prompt_with_page_offset():
    prompt = build_extraction_prompt(page_offset=51, page_count=50)
    assert "51" in prompt
    assert "100" in prompt
