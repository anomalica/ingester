# PDF Ingester Design

## Purpose

The PDF ingester is the first format handler in the anomalica-ingester pipeline. It takes a PDF file (born-digital or scanned) and produces a DoclingDocument JSON file containing the extracted text content, document structure, and metadata. The digester consumes these JSON files as input.

## Decisions

### Output format: DoclingDocument JSON

All ingesters produce DoclingDocument JSON files using the docling-core library (MIT licence, 267 KB). This was chosen after evaluating the landscape of intermediary formats:

- **WebVTT** - W3C standard for timed text. Handles speaker labels and timestamps but has no concept of document-level metadata or provenance. Subtitle format, not a document container.
- **TEI** (Text Encoding Initiative) - academic standard for encoding texts. Has a speech module for speaker-labelled timestamped dialogue. But it is XML, enormously complex (thousands of pages of guidelines), and designed for scholarly annotation rather than data pipeline efficiency.
- **Apache Tika XHTML** - normalises all extracted content to XHTML. Battle-tested in enterprise, handles dozens of formats. But the output is flat with no typed elements - tables become HTML tables, but there is no semantic distinction between a caption and a paragraph.
- **Unstructured.io elements** - outputs typed elements (NarrativeText, Title, Table, ListItem) with metadata. Similar concept to DoclingDocument but less formally specified, no published JSON Schema, and tightly coupled to their processing pipeline.
- **Pandoc AST** - longest-standing universal document intermediate representation. Metadata plus a tree of block and inline elements. No concept of audio, timestamps, or speakers.
- **WARC** (Web ARChive) - industry standard for web archiving (Common Crawl, Internet Archive). Stores raw HTTP request/response pairs. A preservation format, not a content model.
- **NewsML-G2** (IPTC) - news industry standard for exchanging multimedia news. Handles text, images, audio, and video. But it is journalism-specific and XML-heavy.
- **Dublin Core** - standard metadata vocabulary (title, creator, date, source). A vocabulary, not a content format. Useful for field naming conventions.
- **Markdown with YAML frontmatter** - human-readable, git-friendly, trivially parseable. But the body is flat text that loses structural information (can't annotate individual spans with metadata like confidence or page number).

DoclingDocument was chosen because:

- It is a typed, hierarchical content model with provenance metadata
- Its schema natively supports both document elements (paragraphs, headings, tables) and timestamped speaker turns (via TrackSource with start_time, end_time, voice fields)
- The docling-core package is lightweight (267 KB) and decoupled from the full Docling processing pipeline (9.7 GB)
- It has a builder API for constructing documents programmatically (add_text, add_heading, add_table, etc.)
- It serialises to JSON (lossless) or markdown (lossy, for human inspection)
- It has a published JSON Schema with 65 type definitions
- It avoids inventing and maintaining a custom schema
- Implementations exist in Python, TypeScript, and Java

### Extraction method: vision-capable AI model

PDFs are sent directly to a vision-capable AI model for comprehension-based text extraction. This replaces the original plan of using pdftotext/PyMuPDF for born-digital PDFs and Tesseract for scanned PDFs.

Rationale:

- **Raw text extraction** (pdftotext, PyMuPDF) reads the internal PDF text stream in storage order, which often has nothing to do with visual reading order. Page numbers, headers, and footers get injected mid-sentence. Multi-column layouts are read across both columns. Footnotes splice into body text.
- **Character-level OCR** (Tesseract) follows visual reading order, which is better, but it pattern-matches characters without structural comprehension. It misidentifies column boundaries, can't distinguish body text from repeated page elements (headers, footers, page numbers), and flattens tables into nonsensical strings.
- **A vision model** sees the page as a human would. It understands that two columns should be read independently, that "47" at the bottom centre is a page number, that a footnote marker is not body text, and that a table is structured data. It handles born-digital and scanned PDFs identically - including semi-rotated scans, journal articles with complex layouts, and simple Word-to-PDF conversions.

No intermediate rendering step is needed - modern models accept PDFs natively. Note that the Claude API has a hard limit of 100 pages per PDF request. Documents exceeding this are handled by the chunked fallback strategy described below.

### Provider: configurable, defaulting to Claude

The extraction provider is configurable. For development, extraction uses Claude Code in headless mode (`claude --print`), which avoids needing a separate API key and uses the existing subscription. For production, this will switch to the Anthropic API directly. The provider is abstracted so this switch requires no changes to the rest of the code.

```python
class ExtractionProvider:
    def extract(self, pdf_data: bytes) -> ExtractionResult:
        """Send PDF to model, return structured extraction."""
        ...

    def extract_chunk(self, pdf_data: bytes, page_offset: int) -> ExtractionResult:
        """Send a chunk of pages, used in fallback mode.
        page_offset is the 1-based page number of the first page in this
        chunk within the original document. It is included in the extraction
        prompt so the model numbers pages correctly (e.g. a chunk containing
        original pages 51-100 is told "these are pages 51-100")."""
        ...
```

`ExtractionResult` is a Pydantic model matching the extraction schema described in the Architecture section below:

```python
class ElementItem(BaseModel):
    type: str          # heading, paragraph, table, list_item, image_description, redacted
    text: str
    page: int
    page_end: int | None = None
    level: int | None = None        # for headings
    caption: str | None = None      # for tables
    rows: list[list[str]] | None = None  # for tables
    extent: str | None = None       # for redacted sections

class ExtractionResult(BaseModel):
    metadata: dict     # title, authors, date
    elements: list[ElementItem]
```

Both methods accept PDF bytes so they work equally well with a whole file or a chunk split from a larger document by pikepdf.

### Containerisation

Each input format gets its own container, managed by container-magic. The PDF container is lightweight - no GPU required, just Python and a few libraries.

## Architecture

### Project structure

```
anomalica-ingester/
  pdf/
    cm.yaml                  # container-magic config
    workspace/
      ingest_pdf.py          # CLI entry point
      extraction/
        __init__.py
        provider.py          # base class for vision model providers
        claude.py            # Claude implementation
      output/
        __init__.py
        builder.py           # builds DoclingDocument from extraction result
    justfile                 # just ingest-pdf <file>
```

### Data flow

1. CLI receives a PDF file path and an output directory
2. Check page count. If within the provider's limit (100 for Claude), send the whole PDF. Otherwise, split into chunks of 50 pages and process each chunk.
3. The PDF (or each chunk) is sent to the configured provider with a structured extraction prompt
4. If a single-pass call fails due to context limits, fall back to chunked extraction
5. The response is parsed and validated against the extraction schema
6. Chunk results (if any) are stitched together using page-boundary merging heuristics
7. The merged result is used to construct a DoclingDocument via docling-core's builder API
8. The DoclingDocument is saved as JSON to the output directory

### Extraction prompt

The prompt instructs the model to:

- Extract all text content preserving document structure (headings with hierarchy, paragraphs, lists, tables)
- Identify and skip document furniture (page numbers, headers, footers, watermarks)
- Extract tables as structured data (rows and columns), not flattened text
- For images, figures, and diagrams: provide a factual description of what is depicted (images are not extracted due to copyright constraints)
- Mark redacted sections with `[REDACTED]` and estimate the approximate extent (a few words, a sentence, a paragraph, a full page)
- Mark illegible or uncertain text as `[illegible]` or `[partially illegible: best guess here]`
- Note the page number each element appears on
- Return results as JSON matching the extraction schema

The extraction schema is a simplified structure for communicating results from the model:

```json
{
  "metadata": {
    "title": "...",
    "authors": ["..."],
    "date": "..."
  },
  "elements": [
    {"type": "heading", "level": 1, "text": "...", "page": 1},
    {"type": "paragraph", "text": "...", "page": 1, "page_end": 2},
    {"type": "table", "caption": "...", "page": 3, "page_end": 4,
     "rows": [["col1", "col2"], ["val1", "val2"]]},
    {"type": "list_item", "text": "...", "page": 4},
    {"type": "image_description", "text": "...", "page": 5},
    {"type": "redacted", "extent": "paragraph", "page": 6}
  ]
}
```

This intermediate schema is then mapped to DoclingDocument via the builder API. The builder handles internal plumbing (JSON pointers, tree structure, parent/child references).

### Chunked extraction for large documents

The Claude API accepts a maximum of 100 pages per request. Each page costs approximately 1,500 to 3,000 input tokens (the model renders pages internally), so a 100-page document may use 150,000 to 300,000 input tokens before the extraction prompt and output tokens are counted.

Strategy:

1. Check the PDF page count using pikepdf
2. If 100 pages or fewer, attempt single-pass extraction
3. If single-pass fails due to context limits, or if the document exceeds 100 pages, split into chunks of 50 pages using pikepdf and process each chunk separately
4. Stitch chunk results together into a single DoclingDocument

50-page chunks (rather than 100) leave headroom for the extraction prompt and output tokens, and give the model enough context per chunk to understand cross-page elements. If a 50-page chunk itself exceeds context limits (dense pages with complex tables), halve the chunk size and retry. Continue halving until the chunk succeeds or reaches a minimum of 5 pages, at which point the document is flagged as requiring manual intervention.

The cost of a failed single-pass attempt before falling back to chunks is accepted as a reasonable trade-off, since most documents in the test corpus are short and will succeed on the first try.

Page-boundary merging heuristics:

- If a chunk ends mid-sentence (no terminal punctuation) and the next chunk starts with a lowercase letter, merge the paragraph fragments
- Tables with the same column structure across a chunk boundary are merged
- Heading hierarchy carries forward across chunks

After merging, validate that the extraction references every page in the source PDF. Log a warning if any pages appear to be missing.

If these heuristics prove insufficient, a second-pass cleanup can be added later.

## CLI interface

```
just ingest-pdf <input-file> [output-dir]
```

- `input-file` - path to a PDF (required)
- `output-dir` - where to write the JSON output (defaults to current directory)

Output filename mirrors input: `report.pdf` produces `report.json`. If the output file already exists, the tool skips processing (idempotency). Use a `--force` flag to re-process.

Batch processing uses shell tooling:

```bash
for f in documents/*.pdf; do just ingest-pdf "$f" output/; done
```

## Tools

Python 3.10+ (required by docling-core).

- container-magic - containerisation
- docling-core - DoclingDocument schema, builder API, JSON export
- pikepdf - PDF page splitting and page counting
- Claude Code (headless mode) - extraction during development
- Anthropic API - extraction in production

## Not covered

- Other input formats (audio/video, ebooks, web pages) - separate containers with separate designs
- Orchestration layer for routing inputs to the correct container
- The digester's consumption of DoclingDocument JSON files
