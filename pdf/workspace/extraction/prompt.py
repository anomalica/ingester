from __future__ import annotations

EXTRACTION_SCHEMA = """{
  "metadata": {
    "title": "string",
    "authors": ["string"],
    "date": "string or null"
  },
  "elements": [
    {
      "type": "heading | paragraph | table | list_item | image_description | redacted",
      "text": "string",
      "page": "integer",
      "page_end": "integer or null (for multi-page elements)",
      "level": "integer or null (heading level, 1-6)",
      "caption": "string or null (table caption)",
      "rows": "[[string]] or null (table rows including header row)",
      "extent": "string or null (for redacted: a few words | sentence | paragraph | page)"
    }
  ]
}"""


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

    return f"""Extract all content from this PDF into structured JSON.

Rules:
- Preserve document structure: headings (with hierarchy level 1-6), paragraphs, lists, tables
- Skip page furniture: page numbers, headers, footers, watermarks
- Tables: extract as structured data with rows and columns, not flattened text. Include a header row.
- Images, figures, diagrams: do not extract the image. Instead, provide a factual description of what is depicted, using type "image_description".
- Redacted sections: mark with type "redacted", text "[REDACTED]", and estimate the extent (a few words, sentence, paragraph, or page).
- Illegible text: use "[illegible]" or "[partially illegible: best guess here]" in the text field.
- Record the page number for every element.
- For elements spanning multiple pages, set page to where it starts and page_end to where it ends.{page_context}

Return ONLY valid JSON matching this schema:

{EXTRACTION_SCHEMA}"""
