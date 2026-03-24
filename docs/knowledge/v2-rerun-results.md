# V2 Re-run Results (2026-03-24)

Re-ran all 7 test corpus PDFs with updated prompt (file_page/printed_page, better redaction guidance). Validator integrated into pipeline.

## Results

| Document | Pages | Status | Cost | Notes |
|----------|-------|--------|------|-------|
| Fravor written statement | 3 | PASS | $0.29 | Clean extraction |
| Elizondo written testimony | 3 | FAIL | $0.14 | Claude returned install instructions instead of extraction |
| Elizondo resignation FOIA | 11 | PASS | $0.36 | Clean, code fences stripped from v1 |
| Nimitz executive summary | 13 | FAIL | $0.46 | Lost pages 1-5 due to chunk failure cascade |
| DoD IG report | 16 | PASS | $0.57 | All 16 pages, validator false positives now fixed |
| House Oversight hearing | 54 | PASS | $1.16 | All 54 pages, chunk 1 re-split to 10-page chunks |
| AARO historical report | 63 | FAIL | $0.99 | Lost pages 1-20, frontmatter missing |

**Total cost: ~$3.97**

## Issues Found

### Issue 1: Claude sometimes returns garbage instead of extraction

The Elizondo written testimony (3 pages) failed because Claude returned instructions about installing poppler-utils instead of extracting the PDF. This is a non-deterministic failure - the same document extracted fine in v1.

**Recommendation:** Detect non-record output (doesn't start with `---`) and retry once. The validator already catches this ("No YAML frontmatter found").

### Issue 2: Chunk failure cascade loses earlier content

When a chunk fails and gets re-split, the sub-chunks succeed but the output from earlier successful chunks can be lost or the frontmatter from the first chunk gets stripped incorrectly. This affected the Nimitz (pages 1-5 lost) and AARO (pages 1-20 lost) reports.

The root cause is in the chunk merging logic. When chunks fail and get recursively re-split, the content assembly doesn't preserve the relationship between the original chunk sequence and the sub-chunk results.

**Recommendation:** Rewrite the chunk merging to be more robust. Keep all successful chunk outputs in order and only strip frontmatter from chunks that aren't the first in the overall sequence. Or simpler: don't use recursive re-splitting. If a chunk fails, just retry it at the same size before trying to split smaller.

### Issue 3: Non-deterministic extraction quality

The same document can produce different results on different runs. The Elizondo testimony worked in v1 but failed in v2. The Nimitz worked in v1 (single pass, 13 pages) but needed 5-page chunks in v2.

**Recommendation:** Add retry logic before falling back to chunking. If single pass fails, retry once before chunking. This handles transient failures without the complexity of chunk splitting.

### Issue 4: Cross-chunk formatting inconsistency (from v1, not retested)

Different chunks can produce different formatting for the same types of content (e.g. bold speaker names in one chunk, ALL-CAPS in another). This is inherent to chunked extraction - each chunk is an independent Claude session.

**Recommendation:** Accept this for now. A post-processing normalisation step could fix it, but it's not critical for the digester.

## Comparison with v1

- v1: 7/7 succeeded (all used old `page:` field, one had code fences)
- v2: 4/7 succeeded with new `file_page:` field and validator
- The failures are non-deterministic - the documents that failed in v2 worked in v1
- Retry logic would likely fix all three failures

## Next Steps

1. Add retry logic (retry once before falling back to chunking)
2. Fix chunk merging to not lose content on re-split
3. Re-run failed documents
