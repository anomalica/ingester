# PDF Ingester Design

## Purpose

The PDF ingester takes a PDF file (born-digital or scanned) and produces an Anomalica record file (markdown with YAML annotations). The digester consumes these files as input.

## Output format

Anomalica record format - markdown with YAML frontmatter and annotations. See the full specification in the meta-repository at `architecture/record-format.md` and the draft ADR at `decisions/drafts/record-interchange-format.md`.

## Extraction method

PDFs are sent directly to a vision-capable AI model for comprehension-based text extraction. The model sees each page as an image and understands layout, columns, tables, headers, footers, redactions, and figures.

Two providers are supported:

- **Anthropic API** (default when `ANTHROPIC_API_KEY` is set) - single API call per document, no tool use, supports up to 600 pages. Uses streaming for large documents.
- **Claude Code** (fallback) - shells out to `claude --print`. Slower due to multi-turn tool use overhead, but has more permissive content filtering. Used when the API blocks output.

## Project structure

```
pdf/
  cm.yaml                          # container-magic config
  workspace/
    ingest_pdf.py                  # CLI entry point
    validator.py                   # record format validator
    extraction/
      anthropic_api.py             # Anthropic API provider
      claude_code.py               # Claude Code headless provider
      prompt.py                    # extraction prompt template
      chunker.py                   # PDF page splitting
    tests/
```

## CLI

```
cm run ingest input=<pdf-file> output=<output-dir>
```

Or via the root justfile:

```
just test-pdf-extract test-corpus/pdf/<filename>.pdf
```

## Validation

The validator runs after extraction and checks:
- Required frontmatter fields (schema, title, date, source_type)
- Schema version
- Valid YAML in frontmatter
- Page annotation sequence and completeness
- No HTML tags (should use markdown)
- No stale `page:` field (should be `file_page:`)

Auto-fixes: code fence strapping, YAML quoting for values containing colons.
