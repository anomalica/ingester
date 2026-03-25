from __future__ import annotations

EXAMPLE = """---
schema: anomalica/record/1
title: Example Document
date: 2023-07-26
authors:
  - Author Name
source_type: pdf
pages: 3
---

---
file_page: 1
---

# Document Title

First paragraph of text with a footnote reference.[^1]

The programme was conducted at {{redacted: ~2 words}} Air Force Base.

---
file_page: 2
printed_page: 8
---

More text on the second page. The date was {{illegible: possibly March 2004}}.

---
redacted:
  extent: ~2 paragraphs
---

Text continues after the redacted section.

---
image: Description of what the figure shows.
---

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

The format is markdown with YAML frontmatter, YAML block annotations, and inline annotations.

Rules:
- Start with YAML frontmatter: schema, title, date, authors, source_type, pages
- Quote YAML values that contain colons (e.g. title: "Document: A Subtitle")
- Mark page boundaries with YAML block annotations
- file_page is always the PDF page number (1-indexed from the start of the file)
- If the page has a printed page number that differs from file_page, include printed_page
- If there is no printed page number, or it matches file_page, omit printed_page
- Write text as natural markdown (headings, paragraphs, lists, tables, bold, italic)
- Footnotes/endnotes: use markdown footnote syntax [^N] for references and [^N]: text for definitions
- No HTML tags. Use only markdown syntax. No <sup>, <sub>, <br>, or any other HTML.
- Skip page furniture: page numbers, running headers, running footers, watermarks
- Images/figures: YAML block annotation with image field containing a factual description
- Block-level redactions: YAML block annotation with redacted.extent. Be specific about extent (~2 sentences, ~1 paragraph, most of the page). Only use block-level for sentence-sized or larger redactions.
- Inline redactions: {{{{redacted: ~N words}}}} or {{{{redacted}}}} for small mid-sentence redactions
- Illegible text: {{{{illegible: best guess}}}} or {{{{illegible}}}}
- Em-dashes written as --- must be converted to a single hyphen
- schema must be: anomalica/record/1
- source_type must be: pdf{page_context}

Example:

{EXAMPLE}
Return ONLY the markdown. No commentary, no preamble, no postamble."""
