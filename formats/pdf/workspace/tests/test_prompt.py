from extraction.prompt import build_extraction_prompt


def test_prompt_contains_record_format():
    prompt = build_extraction_prompt()
    assert "anomalica/record/1" in prompt
    assert "source_type" in prompt
    assert "file_page" in prompt


def test_prompt_mentions_redaction():
    prompt = build_extraction_prompt()
    assert "redacted" in prompt.lower()


def test_prompt_redaction_wants_char_extent_and_citation():
    """The value carries a CHARACTER estimate (judged off the box, never derived
    from a word count) plus the printed exemption code - both in one
    comma-separated value. line 80 taught the code as the identifying feature
    while the old rule asked for a word count, and the model resolved the pull by
    emitting the code alone: 160 of 271 corpus markers cited a code, not a size."""
    prompt = build_extraction_prompt()
    assert "~N chars" in prompt
    assert "NEVER converted from a word count" in prompt
    assert "comma-separated" in prompt


def test_prompt_struck_classification_banner_is_strikethrough_not_a_tag():
    """A classification banner ruled through by a declassification stroke is a
    strikethrough event, not a live marking. Rule 103 (strike -> ~~) and rule 109
    (do not strike printed markings) collided on a genuinely-struck banner and
    109 won, so the model emitted {{classification: ...}} and lost the strike.
    The banner must be struck text, kept out of the frontmatter classification."""
    prompt = build_extraction_prompt()
    assert "struck banner" in prompt.lower()
    assert "declassification stroke" in prompt
    assert "REMOVED, not one in force" in prompt


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


def test_prompt_teaches_the_document_boundary_annotation():
    """Telling the model only what NOT to do left it with nowhere to put per-paper
    metadata, which is how the fenced blocks happened. The annotation landed in
    ingest-format.md (e157803), so the prompt now names the right place."""
    prompt = build_extraction_prompt()
    assert "<!-- document: {n: 3," in prompt
    assert "NEVER the container's" in prompt


def test_prompt_requires_latex_and_forbids_dollar_delimiters():
    """Dollar delimiters collide with the dollar figures this corpus is full of:
    `$50-$60` opens math at `$5` and closes at the second `$`, turning prose
    between them into an equation."""
    prompt = build_extraction_prompt()
    assert "\\[ ... \\]" in prompt
    assert "\\( ... \\)" in prompt
    assert "NEVER $ or $$" in prompt


def test_prompt_forbids_simplifying_an_equation():
    """Same fidelity bar as a quote - flattening to unicode loses radical scope and
    subscripts, which is a loss of meaning rather than a simplification."""
    prompt = build_extraction_prompt()
    assert "AS PRINTED" in prompt
    assert "do not solve it" in prompt


def test_prompt_keeps_doubled_braces_out_of_math():
    """`x^{{n}}` matches the {{...}} inline-annotation regex, so a consumer parsing
    the body reads part of an equation as an annotation. Prevented at source rather
    than by requiring every consumer to lift math spans before parsing - the same
    argument that chose non-colliding delimiters over an escape mechanism."""
    prompt = build_extraction_prompt()
    assert "NEVER put two braces together" in prompt
    assert "x^{{n}}" in prompt
    assert "x^{ {n} }" in prompt
