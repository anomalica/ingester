from extraction.prompt import build_extraction_prompt


def test_prompt_contains_record_format():
    prompt = build_extraction_prompt()
    assert "anomalica/record/1" in prompt
    assert "source_type" in prompt
    assert "page:" in prompt


def test_prompt_mentions_redaction():
    prompt = build_extraction_prompt()
    assert "redacted" in prompt.lower()


def test_prompt_mentions_image_description():
    prompt = build_extraction_prompt()
    assert "image" in prompt.lower()
    assert "description" in prompt.lower()


def test_prompt_with_page_offset():
    prompt = build_extraction_prompt(page_offset=51, page_count=50)
    assert "51" in prompt
    assert "100" in prompt
