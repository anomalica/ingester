# anomalica-ingester

Converts raw source material (PDFs, audio, video, web pages) into the Anomalica record format for downstream knowledge extraction.

```
anomalica-ingester/
  ingest              - host script: routes by content type
  acquire/            - stage 1: fetch and cache source material
  formats/
    pdf/              - PDF extraction (born-digital and scanned)
    audio/            - audio/video transcription and speaker diarisation
    webpage/          - web page content extraction
    ebook/            - ebook text extraction (planned)
  shared/             - utilities shared across format handlers
  staging/            - transient staging directories (gitignored)
  output/             - extraction output (gitignored)
    store/            - hash-named record files (source of truth)
    records/          - human-readable symlinks
  test-corpus/        - test input files (gitignored, downloaded via justfile)
  docs/
    specs/            - format handler design specifications
    labelling-guide.md - guide for reviewing and labelling records
```

## Usage

```bash
# Ingest any URL or local file
./ingest https://www.youtube.com/watch?v=ZBtMbBPzqHY
./ingest /path/to/document.pdf
./ingest --force https://example.com/article    # re-process even if already in store
```

The `ingest` script acquires the source, detects its type, and routes to the appropriate format handler. Output lands in `output/store/` with a human-readable symlink in `output/records/`.

## Record format

All format handlers produce markdown files with YAML frontmatter and annotations. The full specification is in the [meta-repository](https://github.com/anomalica/anomalica/blob/main/architecture/record-format.md).

## After ingestion

Records need human review before downstream processing. See the [labelling guide](docs/labelling-guide.md) for conventions on speaker identification, naming, and relevance marking.

## Setup

Requires [container-magic](https://github.com/markhedleyjones/container-magic) and Docker. Each format handler has its own container with independent dependencies.

```bash
# Build all containers
cd acquire && cm build && cd ..
cd formats/pdf && cm build && cd ../..
cd formats/audio && cm build && cd ../..
cd formats/webpage && cm build && cd ../..
```

### Environment

Add to `.env` at the repository root:

```
ANTHROPIC_API_KEY=sk-ant-...    # for PDF extraction
HF_TOKEN=hf_...                 # for audio diarisation (pyannote model access)
```

The HF_TOKEN requires a [HuggingFace account](https://huggingface.co/settings/tokens) with access to the [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) gated model.

## Testing

```bash
just test-shared      # shared utilities
just test-acquire     # acquisition layer
just test-webpage     # webpage handler
just test-audio       # audio handler
just test-pdf         # PDF handler (runs in container)
just test-all         # everything
```

## Test corpus

```bash
just download-test-corpus    # downloads publicly available test files
```

Sources are listed in `test-corpus/sources.yaml`.
