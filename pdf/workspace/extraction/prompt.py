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
page: 1
---

# Document Title

First paragraph of text.

The programme was conducted at {{redacted: ~2 words}} Air Force Base.

---
page: 2
---

More text on the second page. The date was {{illegible: possibly March 2004}}.

---
redacted:
  extent: paragraph
---

Text continues after the redacted section.

---
image: Description of what the figure shows.
---

| Column A | Column B |
|----------|----------|
| Value 1  | Value 2  |
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
- Mark page boundaries with YAML block annotations (--- page: N ---)
- Write text as natural markdown (headings, paragraphs, lists, tables, bold, italic)
- Skip page furniture: page numbers, running headers, running footers, watermarks
- Images/figures: YAML block annotation with image field containing a factual description
- Block-level redactions: YAML block annotation with redacted.extent (words, sentence, paragraph, page)
- Inline redactions: {{{{redacted: ~N words}}}} or {{{{redacted}}}} for unknown extent
- Illegible text: {{{{illegible: best guess}}}} or {{{{illegible}}}}
- Em-dashes written as --- must be converted to a single hyphen
- schema must be: anomalica/record/1
- source_type must be: pdf{page_context}

Example:

{EXAMPLE}
Return ONLY the markdown. No commentary, no preamble, no postamble."""
