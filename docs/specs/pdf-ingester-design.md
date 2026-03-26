# PDF Ingester Design

## Purpose

The PDF ingester takes a PDF file (born-digital or scanned) and produces an Anomalica record file (markdown with YAML annotations). The digester consumes these files as input.

## Output format

Anomalica record format - markdown with YAML frontmatter and annotations. See the specification in the meta-repository at `architecture/record-format.md` and the ADR at `decisions/0012-record-interchange-format.md`.

## Extraction method

PDFs are sent directly to a vision-capable AI model. The model sees each page as an image and understands layout, columns, tables, headers, footers, redactions, and figures. No OCR or text extraction tools are used.

## Provider strategy

1. **Anthropic API** (default when `ANTHROPIC_API_KEY` is set) - single API call per document, no tool use. Documents over 50 pages are chunked. Uses streaming to avoid SDK timeout.
2. **Progressive chunking** - if extraction fails (content filtering, timeout, garbage response), chunk size is halved and each chunk retried. Continues down to single pages.
3. **Claude Code** (last resort) - individual pages that fail via the API at every chunk size are sent to Claude Code headless mode, which has more permissive content filtering.
4. **Page repair** - after extraction, the validator identifies missing pages. Missing pages are re-extracted individually and spliced into the output.

## Validation

The validator runs after extraction and checks:
- Required frontmatter fields (schema, title, date, source_type)
- Schema version
- Valid YAML in frontmatter (auto-fixes unquoted colons)
- Page annotation sequence and completeness
- Truncation detection (more than 25% of pages missing is an error)
- No HTML tags (should use markdown)
- No stale `page:` field (should be `file_page:`)
- Code fence wrapping (auto-stripped)

If validation finds errors, the pipeline repairs missing pages and re-validates.

## Project structure

```
pdf/
  cm.yaml
  workspace/
    ingest_pdf.py            # CLI entry point
    validator.py             # record format validator
    extraction/
      __init__.py            # strip_code_fences utility
      anthropic_api.py       # Anthropic API provider
      claude_code.py         # Claude Code headless provider
      prompt.py              # extraction prompt template
      chunker.py             # PDF page splitting and counting
    tests/                   # 60 tests
```

## CLI

```bash
cm run ingest input=<pdf-file> output=<output-dir>
```

## Idempotency

SHA-256 hash of the input PDF is stored in the metadata file. If the output exists and the hash matches, extraction is skipped. Use `--force` to re-extract.
