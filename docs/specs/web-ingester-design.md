# Web Ingester Design

Converts web articles into the Anomalica record format. Fetches HTML, extracts article content and metadata mechanically (no AI), and writes hash-named records with human-readable symlinks.

## Architecture

```
shared/                          # Format-agnostic utilities (new)
  validator.py                   # Record format validation
  record.py                     # Record building, frontmatter, symlink creation
  hashing.py                    # SHA-256, idempotency, output path resolution

web/workspace/
  ingest_web.py                  # CLI entry point, orchestration
  fetch/
    __init__.py                  # fetch(url) dispatcher - tries methods in order
    http.py                      # Simple HTTP fetch
    wayback.py                   # Wayback Machine API lookup and fetch
  extraction/
    __init__.py
    trafilatura_ext.py           # Article text + metadata via trafilatura
  tests/
```

The PDF ingester will be migrated to use `shared/` after the web ingester is built. Both ingesters write to the same `output/` directory.

## Pipeline

1. Receive URL as command argument.
2. **Fetch** - try in order until usable HTML is obtained:
   a. Simple HTTP request
   b. Wayback Machine archived snapshot
3. **Extract** - pass HTML to trafilatura. Returns article text (markdown), title, author(s), date.
4. **Hash** - SHA-256 of the extracted text.
5. **Idempotency check** - if `output/store/{hash}.md` exists, skip.
6. **Build record** - assemble frontmatter + content body.
7. **Validate** - shared validator checks required fields, schema version, no HTML tags, YAML syntax.
8. **Write** - `{hash}.md` and `{hash}.meta.json` to `output/store/`. Create symlink in `output/records/`.

## Fetch layer

Each fetch method has the same interface: takes a URL, returns HTML as a string or None on failure.

The dispatcher calls them in order. A fetch "fails" if:
- The HTTP request fails (timeout, connection error, non-2xx status)
- The response is HTML but trafilatura extracts no meaningful content (paywall, cookie wall, JavaScript-only page)

The second condition means the dispatcher calls trafilatura after each successful HTTP response to check whether the HTML contains extractable article content. If not, it moves to the next fetch method. This keeps the fetch/extraction boundary clean - each fetcher only returns HTML, and the dispatcher uses trafilatura as the quality gate.

### HTTP fetch

- Uses `requests` with a browser-like User-Agent header.
- Follows redirects.
- 30-second timeout.

### Wayback Machine fetch

- Query the Availability API: `https://archive.org/wayback/available?url={url}`
- If a snapshot exists, fetch the archived HTML.
- The archived page is often cleaner than the live site (no dynamic paywalls, no cookie banners).

### Playwright (future)

Not implemented initially. The fetch interface is designed so a Playwright fetcher can be added as a third method when a concrete need arises. Headless Chromium is too heavy to include speculatively.

## Extraction

trafilatura handles both content and metadata extraction from HTML:

- **Content**: article text as markdown. trafilatura strips navigation, ads, sidebars, and boilerplate.
- **Metadata**: title, author(s), date, description, site name. Extracted from OpenGraph tags, schema.org markup, and HTML meta tags.

No AI model is used. Web articles have semantic HTML structure that mechanical extraction handles well.

## Record format

Output follows the Anomalica record format spec (`anomalica/architecture/record-format.md`).

Frontmatter:
```yaml
---
schema: anomalica/record/1
title: "Article title"
date: 2023-06-05
source_type: web
source_url: https://example.com/article
authors:
  - Author Name
content_hash: sha256:abc123...
---
```

Content: the extracted article text as markdown. No page boundary annotations (web articles are continuous). Image descriptions as block annotations where trafilatura identifies figures.

## Output structure

Follows the two-level structure from the record format spec:

```
output/
  store/
    abc123...md
    abc123...meta.json
  records/
    2023-06-05-web-article-title.md -> ../store/abc123...md
```

The `content_hash` is a SHA-256 of the extracted text (not the raw HTML). Raw HTML varies across fetches due to ads and tracking, but the extracted article content is stable.

## Metadata file

```json
{
  "input_url": "https://example.com/article",
  "input_hash": "sha256:abc123...",
  "extracted_at": "2026-03-26T09:28:00Z",
  "fetch_method": "http",
  "duration_ms": 1200,
  "trafilatura_metadata": {
    "title": "Article Title",
    "author": "Author Name",
    "date": "2023-06-05",
    "sitename": "Example News",
    "description": "Brief summary"
  }
}
```

## Shared library (shared/)

Code extracted from the PDF ingester and made format-agnostic:

### validator.py
- Required frontmatter fields: schema, title, date, source_type
- Schema version check
- YAML syntax validation and auto-fix (unquoted colons)
- No HTML tags check
- Code fence stripping
- Source-type-specific checks delegated to the caller (page completeness for PDF, source_url required for web)

### record.py
- Slugify title for symlink naming
- Create `{date}-{source_type}-{slug}.md` symlink in `records/`
- Write record file to `store/`
- Write metadata JSON to `store/`

### hashing.py
- SHA-256 of bytes or string
- Format as `sha256:{hex}`
- Check if hash exists in store (idempotency)

## Container setup

```yaml
names:
  image: anomalica-ingester-web
  workspace: workspace
  user: nonroot

runtime:
  features: []
  volumes:
    - ../output:/mnt/output:rw
    - ../shared:/mnt/shared:ro

stages:
  base:
    from: python:3.12-slim
    steps:
      - pip:
          install:
            - trafilatura
            - requests
            - pyyaml

  development:
    from: base
    steps:
      - pip:
          install:
            - pytest

  production:
    from: base
    steps:
      - copy: workspace

commands:
  ingest:
    command: python workspace/ingest_web.py
    description: Extract structured content from a web article
    env:
      PYTHONUNBUFFERED: "1"
```

The `shared/` directory is mounted read-only into the container. The Python code adds `/mnt/shared` to `sys.path` to import from it.

No Claude Code or Anthropic API key needed - web extraction is entirely mechanical.

## CLI interface

```bash
cd web
cm run ingest https://example.com/article
```

The URL is passed as a positional argument. No `input=` mount needed since the input is a URL, not a file. Output is pre-wired via the volume mount.

Options:
- `--force` - re-extract even if the hash already exists in the store

## Test corpus

6 web articles from `test-corpus/sources.yaml`:

| Article | Date | URL |
|---------|------|-----|
| NYT: Glowing Auras and Black Money | 2017-12-16 | nytimes.com (paywalled, use Wayback Machine) |
| The Intercept: Elizondo sceptical piece | 2019-06-01 | theintercept.com |
| Black Vault: Pentagon spokesperson denials | 2019-2021 | theblackvault.com |
| The Hill: IC Inspector General complaint | 2022-07 | thehill.com |
| The Debrief: Non-human craft retrieval | 2023-06-05 | thedebrief.org |
| Burlison: Grusch special adviser | 2025-03 | burlison.house.gov |

## Error handling

- All fetch methods exhausted: log error, exit with non-zero status.
- trafilatura returns no content: treat as fetch failure, try next method.
- Missing metadata (no author, no date): write the record with what we have. The validator warns but doesn't fail on missing optional fields. The frontmatter `date` is required - if trafilatura can't determine it, fall back to the current date with a warning.
- Network errors: log and try next fetch method.

## What this design does not include

- **Playwright/headless browser**: deferred until a concrete need arises.
- **AI-based extraction**: not needed for web articles with semantic HTML.
- **Batch processing**: one URL per invocation. Batch orchestration is handled externally (justfile, shell loops).
- **Rate limiting or politeness delays**: single-URL invocation, not a crawler.
- **Cookie/login handling**: out of scope. Inaccessible content falls back to Wayback Machine.
