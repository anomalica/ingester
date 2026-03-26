# anomalica-ingester

Converts raw source material into the Anomalica record format for downstream knowledge extraction.

Each input format has its own container-magic project with independent dependencies:

```
anomalica-ingester/
  pdf/          - PDF extraction (born-digital and scanned)
  media/        - audio/video transcription (planned)
  ebook/        - ebook text extraction (planned)
  web/          - web page scraping (planned)
  test-corpus/  - test input files (gitignored, downloaded via justfile)
  output/       - extraction output (gitignored)
```

## Record format

All ingesters produce markdown files with YAML frontmatter and annotations. The format specification is in the meta-repository at `architecture/record-format.md`.

A record file looks like:

```markdown
---
schema: anomalica/record/1
title: Document Title
date: 2023-07-26
authors:
  - Author Name
source_type: pdf
pages: 3
---

---
file_page: 1
---

# Heading

Paragraph text. Any {{redacted: ~3 words}} appear inline.

---
file_page: 2
---

More content on page two.
```

## PDF ingester

### Setup

Requires container-magic and Docker.

```bash
cd pdf
cm build
```

Set `ANTHROPIC_API_KEY` in a `.env` file at the repository root for direct API access (faster, cheaper). Without it, falls back to Claude Code headless mode.

### Usage

```bash
# Via container-magic command
cd pdf
cm run ingest input=/path/to/document.pdf output=/path/to/output/

# Via justfile
just test-pdf-extract test-corpus/pdf/document.pdf
```

Output: `<filename>.md` (the record) and `<filename>.meta.json` (extraction metadata with token counts).

### How it works

1. Sends the PDF to a vision model for comprehension-based extraction
2. Anthropic API by default (single request, no tool use)
3. Documents over 50 pages are chunked, with page annotations renumbered after merge
4. If the API content filter blocks output, progressively halves chunk size down to single pages
5. Individual pages that the API cannot process fall back to Claude Code
6. Validates output: checks frontmatter, page completeness, no HTML tags
7. Repairs missing pages by re-extracting just those pages
8. Hash-based idempotency: skips re-extraction if the input file hasn't changed

### Test corpus

```bash
just download-test-corpus    # downloads publicly available test PDFs
```

Sources are listed in `test-corpus/sources.yaml`. Some files require manual download (defence.gov blocks automated requests).

## Adding a new format

Each format is a separate container-magic project:

1. Create a directory (e.g. `ebook/`)
2. Add `cm.yaml` with dependencies
3. Implement extraction that produces the record format
4. Add a validator if the format has specific requirements
5. Add test corpus entries to `test-corpus/sources.yaml`
