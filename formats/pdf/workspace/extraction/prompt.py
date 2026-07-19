from __future__ import annotations

EXAMPLE = """---
schema: anomalica/record/1
title: "Example Document"
date_published: 2023-07-26
authors:
  - Author Name
source_type: pdf
pages: 3
classification: "SECRET//REL TO USA, FVEY"
---

<!-- file_page: 1 -->

# Document Title

First paragraph of text with a footnote reference.[^1]

The programme was conducted at {{redacted: ~2 words}} Air Force Base.

<!-- file_page: 2 -->
<!-- printed_page: 8 -->

More text on the second page. The date was {{illegible: possibly March 2004}}.

<!--
redacted:
  extent: ~2 paragraphs
-->

Text continues after the redacted section.

<!-- image: Description of what the figure shows. -->

| Column A | Column B |
|----------|----------|
| Value 1  | Value 2  |

[^1]: Source citation or footnote text here.
"""


def build_extraction_prompt(
    page_offset: int | None = None, page_count: int | None = None
) -> str:
    page_context = ""
    if page_offset is not None and page_count is not None:
        page_end = page_offset + page_count - 1
        page_context = (
            f"\n\nThese are pages {page_offset} to {page_end} of a larger document. "
            f"Number pages starting from {page_offset}."
        )

    return f"""Extract all content from this PDF into the Anomalica record format.

The format is markdown with YAML frontmatter and HTML comment annotations.

Transcribe the document faithfully. Reproduce only the content and structure that is actually present in the source - your job is to represent the document as it is, not to organise or improve it. Do not invent, add, group, summarise, or relabel anything.

Rules:
- FAITHFULNESS (most important): never add structure the source does not contain. No section headings, no category or grouping labels (e.g. "Majority Members:", "Attendees:", "Summary:", "Background:"), no tables, and no bold/italic emphasis unless that exact heading, label, table, or emphasis is printed in the document. Do not reorganise or re-lay-out the content; keep the source's own order and wording. When the source uses a visual layout you cannot reproduce (multi-column lists, side-by-side rosters), transcribe the text in natural reading order WITHOUT adding labels to explain the layout.
- Start with YAML frontmatter: schema, title, date_published, authors, source_type, pages
- Always quote the title value (e.g. title: "Document Title")
- Quote any YAML values that contain colons
- Mark page boundaries with single-line HTML comments: <!-- file_page: 1 -->
- file_page is always the PDF page number (1-indexed from the start of the file)
- If the page has a printed page number that differs from file_page, add <!-- printed_page: 8 --> on the next line
- If there is no printed page number, or it matches file_page, omit printed_page
- Use markdown only to MIRROR structure the source actually has: a heading printed in the document becomes a markdown heading, a table in the document becomes a markdown table, text emphasised in the document becomes bold/italic. Plain prose stays plain prose. Do not add structure the source lacks (see FAITHFULNESS above)
- Footnotes/endnotes: use markdown footnote syntax [^N] for references and [^N]: text for definitions
- No HTML tags. Use only markdown syntax. No <sup>, <sub>, <br>, or any other HTML.
- Do not write HTML comments except for annotations (page boundaries, images, redactions)
- Skip page furniture: page numbers, running headers, running footers, watermarks
- Images/figures: single-line HTML comment with image field: <!-- image: Factual description -->
- Block-level redactions: multi-line HTML comment with redacted.extent. Be specific about extent (~2 sentences, ~1 paragraph, most of the page). Only use block-level for sentence-sized or larger redactions.
- Inline redactions: {{{{redacted: ~N words}}}} or {{{{redacted}}}} for small mid-sentence redactions
- Illegible text: {{{{illegible: best guess}}}} or {{{{illegible}}}}
- Classification markings (for declassified government documents):
  - The document's overall banner goes in frontmatter as `classification:` with the verbatim marking minus the surrounding parentheses, e.g. classification: "SECRET//REL TO USA, FVEY". Omit the field entirely if the document is unmarked or unclassified.
  - Repeated in-body copies of that same overall banner (page headers/footers) are redundant - drop them, don't reproduce them in the prose.
  - Per-portion markings that DIFFER from the overall banner (paragraph/section prefixes like "(U)", "(S//REL)", "(S/RELIDO)") become an inline annotation at the start of the portion they govern: {{{{classification: U}}}}, {{{{classification: S//REL}}}}, {{{{classification: "S/RELIDO"}}}}. Quote the value if it contains a colon or comma. The marking applies from its position until the next classification marking.
  - Reproduce marking values verbatim (minus parens). Do not normalise or expand them.
  - NEVER render classification markings as strikethrough. Strikethrough (~~text~~) is reserved for text genuinely struck out in the source.
- Em-dashes written as --- must be converted to a single hyphen
- Title: use the document's actual title or subject - the core title only. Do NOT append event metadata that sits near the title on a cover page but is not part of it: conference/meeting dates, venue, city, or location (e.g. drop trailing ", February 22-24, 2012. The Westin Tysons Corner, Falls Church, VA."). Keep any real subtitle. Never put the literal words "undefined", "null", or "None" in the title - if part of a title is missing or unreadable, omit that part rather than writing a placeholder word.
- schema must be: anomalica/record/1
- source_type must be: pdf{page_context}

Example:

{EXAMPLE}
Return ONLY the markdown. No commentary, no preamble, no postamble."""
