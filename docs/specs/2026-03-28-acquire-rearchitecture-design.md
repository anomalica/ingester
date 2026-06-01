# Acquire Re-architecture Design

Restructures the ingester from format-specific entry points into a three-layer architecture: a host-level orchestrator, an acquisition container that fetches and classifies content, and format-specific containers that process each content type.

## Motivation

The web ingester's fetch chain (HTTP, Wayback Machine, Patchright) is useful for acquiring any content type, not just HTML. A URL that points to a PDF should be handled transparently without the caller needing to know the content type in advance. The current architecture requires choosing the right ingester upfront.

## Architecture

Three layers:

1. **Host script** (`ingest`) - thin bash script, runs on the host, orchestrates the pipeline
2. **Acquire container** (`acquire/`) - fetches the asset via the fallback chain, detects its content type, writes it to a staging directory
3. **Format containers** (`formats/*/`) - each handles a specific content type, reads from staging, writes records to output

```
./ingest <url-or-path>
  |
  |  (local path?)
  |  yes -> detect type from extension/magic bytes, copy to staging
  |  no  -> call acquire container
  |
  v
acquire/run.sh <url> --staging-dir staging/{uuid}
  |
  |  HTTP -> Wayback -> Patchright (fallback chain)
  |  detect content type, write asset + manifest.json
  |
  v
host reads staging/{uuid}/manifest.json
  |
  |  scan formats/*/format.yaml for matching MIME type
  |
  v
formats/{match}/run.sh staging/{uuid}
  |
  |  process asset, write record to output/
  |
  v
done
```

## Directory structure

```
ingester/
  ingest                          # host script (bash)
  acquire/                        # container-magic project
    cm.yaml
    workspace/
      acquire.py                  # CLI entry point
      fetch/
        __init__.py               # ordered dispatcher
        http.py                   # simple HTTP with browser User-Agent
        wayback.py                # Wayback Machine API + snapshot fetch
        patchright_fetch.py       # headless Chromium fallback
      detect.py                   # content type detection
      tests/
  formats/
    webpage/                      # container-magic project
      cm.yaml
      format.yaml                 # declares handled MIME types
      workspace/
        ingest_webpage.py         # CLI entry point
        extraction/
          trafilatura_ext.py      # article text + metadata extraction
        tests/
    pdf/                          # container-magic project
      cm.yaml
      format.yaml                 # declares handled MIME types
      workspace/
        ingest_pdf.py             # existing, adapted for staging input
        extraction/               # existing providers unchanged
        tests/
    media/                        # future
      format.yaml
    ebook/                        # future
      format.yaml
  shared/                         # format-agnostic utilities
    hashing.py
    record.py
    validator.py
    tests/
  staging/                        # gitignored, transient
    {uuid}/
      asset.{ext}
      manifest.json
  output/                         # final records
    store/
    records/
  test-corpus/
```

## Staging directory

Each acquisition run creates a UUID-named subdirectory under `staging/`. This directory contains:

- The raw fetched asset (e.g. `asset.html`, `asset.pdf`)
- A manifest describing what was fetched and how

The staging directory persists after processing for debugging and inspection. It is gitignored. Users can clean it up manually when no longer needed.

### Manifest format

```json
{
  "source": "https://example.com/article",
  "asset": "asset.html",
  "detected_type": "text/html",
  "fetch_method": "http",
  "fetched_at": "2026-03-28T10:00:00Z",
  "response_headers": {
    "content-type": "text/html; charset=utf-8"
  }
}
```

For local file inputs, the manifest records the original path instead of a URL, and `fetch_method` is `"local"`.

## Format self-discovery

Each format handler declares the MIME types it handles in a `format.yaml` file:

```yaml
# formats/webpage/format.yaml
name: webpage
handles:
  - text/html
  - application/xhtml+xml
```

```yaml
# formats/pdf/format.yaml
name: pdf
handles:
  - application/pdf
```

The host script builds its routing table at runtime by scanning `formats/*/format.yaml`. Adding a new format handler requires only adding the directory with a `format.yaml` and a container-magic project. No changes to the host script or acquire container.

## Content type detection

Acquire uses an ordered strategy to determine content type:

1. **Content-Type header** from the HTTP response (most reliable for remote URLs)
2. **Magic bytes** - `%PDF-` for PDF, `<!DOCTYPE`/`<html` for HTML, file signatures for audio/video formats
3. **File extension** - fallback for local file paths or ambiguous responses

For local file inputs, the host script performs detection directly (no acquire container needed) using magic bytes and extension.

## Fetch fallback chain

The acquisition container tries fetchers in order:

1. **HTTP** - simple GET with browser User-Agent, 30-second timeout
2. **Wayback Machine** - query archive.org availability API, fetch closest snapshot
3. **Patchright** - headless Chromium with new headless mode, networkidle with domcontentloaded fallback

The quality gate between attempts changes from the current design. Previously, trafilatura was used as a quality check after each fetch (specific to HTML). Now the gate is format-agnostic:

- A successful response with a recognisable content type (PDF, HTML with reasonable size, audio/video) is accepted
- A 403, CAPTCHA page, or empty response triggers the next fetcher
- A valid PDF on the first HTTP attempt stops the chain immediately - no need to try Wayback or Patchright

Patchright is skipped for non-HTML content types since it renders the DOM rather than downloading raw files.

## Host script

The `ingest` script at the repository root is the single entry point:

```bash
./ingest https://example.com/article
./ingest /path/to/document.pdf
./ingest --force https://example.com/article
```

Behaviour:

1. Generate a UUID for this run
2. If the input is a local path: detect content type from magic bytes and extension, copy the file into `staging/{uuid}/`, write a manifest
3. If the input is a URL: call `acquire/run.sh <url> --staging-dir staging/{uuid}`
4. Read `staging/{uuid}/manifest.json` to get the detected MIME type
5. Scan `formats/*/format.yaml` to find a handler for that MIME type
6. If no handler found: report error and exit
7. Call `formats/{handler}/run.sh staging/{uuid}`
8. Report the result (record path, symlink path)

Options:
- `--force` - passed through to the format ingester, re-processes even if the content hash exists in the store

## Container dependencies

**acquire**: `requests`, `patchright`, playwright Chromium, `pyyaml`

**formats/webpage**: `trafilatura`, `pyyaml` (lightweight, no browser dependencies)

**formats/pdf**: `pikepdf`, `pyyaml`, `anthropic` (unchanged from current)

**shared**: mounted read-only into each format container as before

## Changes to existing code

### Web ingester becomes webpage format handler

- Fetch modules (`http.py`, `wayback.py`, `patchright_fetch.py`, `fetch/__init__.py`) move to `acquire/workspace/fetch/`
- Extraction module (`trafilatura_ext.py`) moves to `formats/webpage/workspace/extraction/`
- `ingest_web.py` becomes `ingest_webpage.py`, modified to read pre-fetched HTML from staging instead of fetching directly
- Fetch-related tests move to `acquire/workspace/tests/`
- Extraction and orchestration tests move to `formats/webpage/workspace/tests/`
- The `web/` directory is removed

### PDF ingester moves under formats

- `pdf/` moves to `formats/pdf/`
- `ingest_pdf.py` gains an alternative input mode: accept a staging directory path in addition to a direct file path
- A `format.yaml` is added declaring `application/pdf`
- Existing tests are adapted for the new path structure

### Shared library

Unchanged. Continues to be mounted read-only into format containers.

## Volume mounts

### acquire container

```yaml
runtime:
  volumes:
    - ../staging:/mnt/staging:rw
```

### format containers (example: webpage)

```yaml
runtime:
  volumes:
    - ../staging:/mnt/staging:ro
    - ../../output:/mnt/output:rw
    - ../../shared:/mnt/shared:ro
```

The staging mount is read-only for format containers - they consume the asset but do not modify it.

## Testing

- **acquire**: fetch chain tests (migrated from current web fetch tests), content type detection tests, manifest writing tests
- **formats/webpage**: trafilatura extraction tests, record writing and orchestration tests (migrated from current web tests, minus fetch tests)
- **formats/pdf**: existing tests adapted for staging input path and new directory structure
- **shared**: unchanged
- **host script**: integration tests that verify routing from URL to correct format handler

## Error handling

- All fetch methods exhausted: acquire writes a manifest with `"detected_type": null` and a `"error"` field. Host script reports failure.
- No format handler matches the detected type: host script reports the type and exits with error.
- Format ingester fails: exit code propagated through the host script.
- Local file not found or unreadable: host script reports error before staging.

## What this design does not include

- **Batch processing**: one URL or file per invocation. Batch orchestration remains external (justfile, shell loops).
- **Rate limiting**: single invocation, not a crawler.
- **Cookie or login handling**: out of scope. Inaccessible content falls back through the fetch chain.
- **Media or ebook format handlers**: declared as future placeholders with `format.yaml` only.

## Migration notes

- The root `justfile` recipes (`ingest-pdf`, `test-web-extract`, `test-web-corpus`) need updating to reflect the new paths and the `./ingest` entry point.
- The existing `web/` directory is removed entirely after migration. `pdf/` moves to `formats/pdf/`.
- Existing output in `output/store/` and `output/records/` remains valid and unchanged.
