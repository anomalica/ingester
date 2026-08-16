from extraction.prompt import build_extraction_prompt


def test_prompt_contains_record_format():
    prompt = build_extraction_prompt()
    assert "anomalica/record/1" in prompt
    assert "source_type" in prompt
    assert "file_page" in prompt


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


def test_prompt_chunk_numbers_from_one_not_absolute():
    """The chunk must be numbered 1-based; the document offset is applied by code
    afterwards. Telling the model to number from page_offset makes it emit
    absolute numbers that the merge then double-counts."""
    prompt = build_extraction_prompt(page_offset=21, page_count=20)
    assert "starting from 21" not in prompt
    assert "from 1" in prompt


def test_prompt_file_page_is_not_printed_number():
    prompt = build_extraction_prompt()
    line = next(line for line in prompt.splitlines() if line.startswith("- file_page"))
    assert "printed" in line.lower()
    assert "position" in line.lower()


def test_prompt_forbids_a_second_frontmatter_block_in_the_body():
    """A compiled document (proceedings, a FOIA release) has real internal document
    boundaries with per-item titles and authors. Given no rule, the model invented a
    fenced metadata block at each one - 17 of them in a 416-page proceedings - which
    put `schema:` and `source_type:` into the body as content, because a `***` fence
    is a thematic break and everything inside it is evidence."""
    prompt = build_extraction_prompt()
    assert "ONE frontmatter block" in prompt
    assert "COMPILED document" in prompt
    assert "never appear in the body" in prompt
