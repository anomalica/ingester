# Anomalica ingester

The shared instructions in `/home/mark/repos/anomalica/AGENTS.md` apply. If they are not already in the current context, read that file before working here.

The ingester converts source material such as PDFs, audio, video, ebooks and web pages into Anomalica records: Markdown with YAML frontmatter and body annotations.

## Contracts and layout

- The canonical output contract is `/home/mark/repos/anomalica/anomalica/architecture/ingest-format.md`. Read it before changing record output and update it in the same change when the contract changes.
- `acquire/` fetches and caches source material and writes a staging manifest.
- `formats/` contains the format-specific ingestion paths.
- `shared/` contains common record, hashing and validation utilities.
- Output records go to the sibling `/home/mark/repos/anomalica/ingests/` repository. Original source files go to `/home/mark/repos/anomalica/records/`.
- Each source produces one record. Do not create separate audio and video records for the same source merely because both tracks exist.
- Preserve content addressing, source and snapshot hashes, verification sidecars and copyright metadata. Check downstream consumers before changing them.

## Providers and secrets

- Provider selection is an implementation concern governed by the central model policy and the private operations billing decision. Do not encode a current provider, price or model as permanent guidance here.
- Preserve the cost-estimate and explicit-confirmation gate on every metered path, including repair and fallback calls.
- Generate local environment files through `just env`; the Safe remains canonical. Do not commit `.env`.

## Running and verification

```bash
./ingest <url-or-path>
./ingest --force <url-or-path>
just env
just test-shared
just test-acquire
just test-webpage
just test-audio
just test-pdf
just test-ebook
just test-all
```

- Run the focused suite for the format changed. Use `just test-all` when shared acquisition, record or validation behaviour changes across formats.
- PDF and ebook tests run through container-magic; the other listed suites run from the host Python environment.
- Never use a paid corpus run as a test. Unit and fixture tests must not write fabricated rows to the production AI-operation ledger.
