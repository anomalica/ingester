# PDF Extraction

Observations from running the PDF ingester against the test corpus.

## Page limits

| Pages | Size | Document | Result |
|-------|------|----------|--------|
| 3 | 63K | Fravor written statement | Single pass, $0.12 |
| 3 | 71K | Elizondo written testimony | Single pass, $0.19 |
| 11 | 2.3M | Elizondo resignation (FOIA, scanned) | Single pass, $0.34 |
| 13 | 4.7M | Nimitz executive summary (scanned) | Single pass, $0.50 |
| 16 | 2.6M | DoD IG UAP report | Single pass, $0.29 |
| 54 | 161K | House Oversight hearing transcript | 3x 20-page chunks, $1.14 |
| 63 | 710K | AARO historical record report vol 1 | 4x 20-page chunks, $1.25 |

**Total test corpus: 7 PDFs, all extracted successfully. Total cost: ~$3.83.**

Current `MAX_PAGES_SINGLE_PASS` is 20. Chunk size is 20.

- 16 pages works as single pass
- 54 pages timed out on single pass, works chunked
- Untested between 16 and 54 for single pass
- 20-page chunks work reliably
- Scanned documents (Nimitz 4.7MB, Elizondo FOIA 2.3MB) extract just as well as born-digital

## Cost per page

Roughly $0.03-0.04 per page based on small documents. Likely decreases for larger documents due to prompt caching.

## Claude Code overhead

Claude Code uses multiple turns (6-10) even for simple extractions. It tries Bash tools (denied), then falls back to Read. The prompt instructs it to use Read directly, which reduced turns from 10 to 6.

The `result` field in the Claude Code envelope contains a prose summary that wastes tokens. Instructing "return ONLY the markdown" helps but doesn't eliminate it entirely.

## Code fence wrapping

Claude sometimes wraps output in ` ```markdown ``` ` code fences. The provider strips these automatically.

## FOIA documents

Claude handles FOIA releases well - correctly extracts cover letters, classification markings, redacted portions, and multiple sub-documents within a single PDF. It uses our `{{redacted}}` inline syntax naturally for classification markers like `(U/{{redacted}})`.
