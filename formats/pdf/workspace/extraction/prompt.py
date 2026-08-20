from __future__ import annotations

EXAMPLE = """---
schema: anomalica/record/1
title: "Example Document"
date_published: 2023-07-26
creators:
  - Author Name
source_type: pdf
pages: 3
classification: "SECRET//REL TO USA, FVEY"
release:
  declassified_by: "Richard A. Harrison"
  declassified_by_title: "MG, USCENTCOM Chief of Staff"
  control_number: "USCENTCOM 26-0028"
  released_to: "AARO"
  release_date: 2026-03-16
  handling: ["FOUO", "PA applies"]
  markings:
    - "Declassified by MG Richard A. Harrison, USCENTCOM Chief of Staff"
    - "FOUO/PA applies"
    - "Approved for Release to AARO"
---

<!-- file_page: 1 -->

# Document Title

First paragraph of text with a footnote reference.[^1]

The programme was conducted at {{redacted: ~9 chars, (b)(1)}} Air Force Base.

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
            f"\n\nThese are pages {page_offset} to {page_end} of a larger document, "
            f"given to you as a standalone {page_count}-page excerpt. Number the "
            f"file_page annotations of THIS excerpt sequentially from 1 (1 for the "
            f"first page shown here, 2 for the next, and so on) - do NOT start from "
            f"{page_offset}; the document offset is added afterwards, so adding it "
            f"yourself would double-count it."
        )

    return f"""Extract all content from this PDF into the Anomalica record format.

The format is markdown with YAML frontmatter and HTML comment annotations.

Transcribe the document faithfully. Reproduce only the content and structure that is actually present in the source - your job is to represent the document as it is, not to organise or improve it. Do not invent, add, group, summarise, or relabel anything.

Rules:
- FAITHFULNESS (most important): never add structure the source does not contain. No section headings, no category or grouping labels (e.g. "Majority Members:", "Attendees:", "Summary:", "Background:"), no tables, and no bold/italic emphasis unless that exact heading, label, table, or emphasis is printed in the document. Do not reorganise or re-lay-out the content; keep the source's own order and wording. When the source uses a visual layout you cannot reproduce (multi-column lists, side-by-side rosters), transcribe the text in natural reading order WITHOUT adding labels to explain the layout.
- CONVEY MEANING, NOT PIXELS: the record must let a reader understand what the page said and meant. Prose is transcribed literally, as above. But where a literal copy of a STRUCTURED layout would lose the meaning - a form, a fielded layout, a table - interpret the layout just enough to make the relationships clear. This represents what is printed so it reads clearly; it never invents content or adds facts that are not on the page.
- FORMS AND FIELDED LAYOUTS: render each printed field as `Label: value` - a colon separating the field's printed label from what was entered on it (e.g. `Rank: O-3`, `Date (mm/dd/yy): 09/04/20`), keeping any printed example hint inside the label (`Contact Altitude (Ex: 22000): 23819`). This is the one case where pairing a printed label with its value takes priority over reading-order literalism, because the pairing IS the meaning. A field left blank or unfilled is `Label:` with nothing after the colon - preserving both the question and that it was not answered. For a checkbox, put its state after the label (`Round: ☑`, `Square: ☐`).
- REDACTION vs BLANK: a {{{{redacted}}}} marker is ONLY for content that has visibly been REMOVED - a bar over text, a box carrying an exemption code (e.g. (b)(1), (b)(6), 3.5c), or an obscured region. When the box prints such a code, that code is part of the redaction record - carry it in the marker value (see Inline redactions). A field simply left empty or unfilled is NOT a redaction.
- A declassification or release stamp repeated on every page is captured ONCE (in the `release:` block); do not reproduce it in the body on each page.
- Start with YAML frontmatter: schema, title, date_published, creators, source_type, pages
- ONE frontmatter block, at the very top, and nothing frontmatter-shaped anywhere below it. Never open a second metadata block mid-document, fenced or otherwise. `schema`, `source_type` and `pages` describe this record, not anything the document says, so they must never appear in the body. (A 416-page proceedings came back with 17 fenced metadata blocks mid-body, which put this vocabulary into the extracted evidence.)
- COMPILED documents - a proceedings of separate papers, an anthology, a report bundling annexes by different authors - mark each contained work with a document annotation: <!-- document: {{n: 3, title: "Apparent Endless Extraction of Energy from the Vacuum", creators: ["Robert L. Forward"], document_type: report}} -->. One annotation carrying one YAML mapping. `n` counts the contained works from 1; `title` and `creators` are that work's own, NEVER the container's; omit `document_type` where it adds nothing. Put it where the work starts - a "NEXT DOCUMENT" page, a title page, a new byline. This is the only place per-item metadata belongs, and the title and byline printed on the page are still transcribed as content where they appear.
- creators: the document's human author(s), one named person per list item. Omit creators entirely when the author is an organisation, agency, military unit, or office rather than a named person (e.g. "89 ATKS", "Department of Defense") - never put a non-person there.
- Always quote the title value (e.g. title: "Document Title")
- Quote any YAML values that contain colons
- Mark page boundaries with single-line HTML comments: <!-- file_page: 1 -->
- file_page is the SEQUENTIAL POSITION of the page counting from 1 (the first page you are given is 1, the next is 2, and so on). It is NOT a number printed anywhere on the page - never copy a page number that appears in the document into file_page. If the page shows its own printed number, that printed number goes in printed_page (below), and file_page stays the sequential position.
- If the page has a printed page number that differs from file_page, add <!-- printed_page: 8 --> on the next line
- If there is no printed page number, or it matches file_page, omit printed_page
- Use markdown only to MIRROR structure the source actually has: a heading printed in the document becomes a markdown heading, a table in the document becomes a markdown table, text emphasised in the document becomes bold/italic. Plain prose stays plain prose. Do not add structure the source lacks (see FAITHFULNESS above)
- Mathematics: transcribe equations as LaTeX - \\[ ... \\] for a display equation, \\( ... \\) inline. NEVER $ or $$: this corpus is full of dollar figures and a range like $50-$60 would be read as an equation. Reproduce the equation AS PRINTED - do not solve it, rearrange it, normalise the notation, or drop an equation number (keep it as \\tag{{4}}). Flattening to unicode is a loss of meaning rather than a simplification: `r = R · √β · Fsky · Ω/(Fsun · α)` no longer says whether the radical covers β alone or the whole product, and its subscripts are gone. The same fidelity bar applies as to a quote.
- Inside a math span, NEVER put two braces together. `x^{{{{n}}}}` collides with the {{{{...}}}} inline-annotation grammar, so a consumer parsing the body reads it as an annotation and mangles the equation. Separate them with a space - `x^{{ {{n}} }}` - which TeX renders identically. Applies to both `{{{{` and `}}}}`; redundant grouping is usually unnecessary anyway (`x^{{n}}` is the same as `x^n`).
- Footnotes/endnotes: use markdown footnote syntax [^N] for references and [^N]: text for definitions
- No HTML tags. Use only markdown syntax. No <sup>, <sub>, <br>, or any other HTML.
- Do not write HTML comments except for annotations (page boundaries, images, redactions)
- Skip decorative page furniture: page numbers, running headers, running footers, decorative watermarks. EXCEPTION: release/declassification provenance (declassification overlays, handling caveats, release-control footers) is NOT furniture even though it sits in footers and overlay stamps - capture it in the `release:` block (see below); never skip it.
- Images/figures: single-line HTML comment with image field: <!-- image: Factual description -->
- Block-level redactions: multi-line HTML comment with redacted.extent. Be specific about extent (~2 sentences, ~1 paragraph, most of the page). Only use block-level for sentence-sized or larger redactions.
- Inline redactions: {{{{redacted: ~N chars, CODE}}}} - the value carries two things, comma-separated, both optional: (1) the box's width estimated in CHARACTERS of the surrounding text, judged from the drawn box ITSELF and NEVER converted from a word count (a fabricated char count that is really a word guess times six is worse than the honest word count); and (2) any exemption or classification code printed in or beside the box, verbatim as printed (e.g. (b)(6), 1.4a, 3.5c), comma-separating several as the source lists them. Write {{{{redacted: ~8 chars}}}} when no code is printed, {{{{redacted: (b)(6)}}}} when the box width cannot be judged, and {{{{redacted}}}} for a bare small redaction.
- Illegible text: {{{{illegible: best guess}}}} or {{{{illegible}}}}
- Struck-out text: TRANSCRIBE text that is struck through (crossed out) in the source, wrapping it in ~~strikethrough~~. Do NOT omit it. A deletion is content, not an instruction to skip - what was struck and the fact that it was struck are both part of the record, and in an edited or declassified document the struck wording is often the point. Keep the struck text in its place in reading order; only the ~~ markers signal that it was crossed out.
- Classification markings (for declassified government documents):
  - The document's overall banner goes in frontmatter as `classification:` with the verbatim marking minus the surrounding parentheses, e.g. classification: "SECRET//REL TO USA, FVEY". Omit the field entirely if the document is unmarked or unclassified.
  - Repeated in-body copies of that same overall banner (page headers/footers) are redundant - drop them, don't reproduce them in the prose.
  - Per-portion markings that DIFFER from the overall banner (paragraph/section prefixes like "(U)", "(S//REL)", "(S/RELIDO)") become an inline annotation at the start of the portion they govern: {{{{classification: U}}}}, {{{{classification: S//REL}}}}, {{{{classification: "S/RELIDO"}}}}. Quote the value if it contains a colon or comma. The marking applies from its position until the next classification marking.
  - Reproduce marking values verbatim (minus parens). Do not normalise or expand them.
  - NEVER render classification markings as strikethrough. Strikethrough (~~text~~) is reserved for text genuinely struck out in the source.
- Release and declassification provenance (STAMPED on the page - a declassification overlay, a handling caveat, a release-control footer - as opposed to the classification banner above, which is a separate thing): capture it in a top-level `release:` frontmatter block. This is documented provenance for the project, not furniture.
  - Fields, include those the page actually shows: `declassified_by` (the releasing officer as a PERSON NAME ALONE, e.g. "Richard A. Harrison" - not their rank or post), `declassified_by_title` (rank and post, e.g. "MG, USCENTCOM Chief of Staff"), `control_number` (e.g. "USCENTCOM 26-0028"), `released_to` (e.g. "AARO"), `release_date` (ISO date), `handling` (a YAML list, e.g. ["FOUO", "PA applies"]).
  - `markings`: REQUIRED whenever a `release:` block is present - a YAML list of the release/declassification stamps VERBATIM as printed. If you have examined the pages and there are NO release or declassification stamps, emit `markings: []` - that asserts examined-and-none-found and is meaningful, so include the block with an empty markings list rather than omitting it.
  - This is DISTINCT from `classification:` (what the document was marked). Do not copy a classification banner into `release.markings`.
  - A Bates-style sequence number in a release footer (e.g. "000001") numbers THAT page, not the document - put it on that page's boundary annotation if anywhere, never in the `release:` block.
- Em-dashes written as --- must be converted to a single hyphen
- Title: use the document's actual title or subject - the core title only. Do NOT append event metadata that sits near the title on a cover page but is not part of it: conference/meeting dates, venue, city, or location (e.g. drop trailing ", February 22-24, 2012. The Westin Tysons Corner, Falls Church, VA."). Keep any real subtitle. Never put the literal words "undefined", "null", or "None" in the title - if part of a title is missing or unreadable, omit that part rather than writing a placeholder word.
- schema must be: anomalica/record/1
- source_type must be: pdf{page_context}

Example:

{EXAMPLE}
Return ONLY the markdown. No commentary, no preamble, no postamble."""
