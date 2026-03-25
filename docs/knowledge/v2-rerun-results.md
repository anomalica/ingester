# PDF Extraction Results

## Final results (API provider)

All 7 test corpus PDFs pass validation.

| Document | Pages | Provider | Input tokens | Output tokens | Status |
|----------|-------|----------|-------------|--------------|--------|
| Fravor written statement | 3 | API | 7,384 | 1,993 | PASS |
| Elizondo written testimony | 3 | API | ~7k | ~2k | PASS |
| Elizondo resignation FOIA | 11 | API | ~20k | ~6k | PASS |
| DoD IG report | 16 | API | ~35k | ~8k | PASS |
| Nimitz executive summary | 13 | Claude Code (content filtered) | ~150k | ~3k | PASS (warning: 3 missing page annotations) |
| House Oversight hearing | 54 | API | 123,337 | 37,002 | PASS |
| AARO historical report | 63 | API | 139,424 | 38,369 | PASS |

## API vs Claude Code comparison

| Metric | Claude Code | Anthropic API |
|--------|-----------|---------------|
| Turns per extraction | 6-10 | 1 |
| 54-page document | ~$1.16, multiple retries, formatting inconsistencies | Single call, consistent formatting |
| 63-page document | Multiple failures, missing pages, ~$1.25-1.88 | Single call, all pages, first try |
| Reliability | Non-deterministic, stub responses, timeouts | Consistent |
| Content filtering | Permissive | Strict (1 document blocked) |

## Evolution

- v1 (Claude Code, DoclingDocument JSON): 7/7 passed but output was bloated
- v2 (Claude Code, markdown format): 4/7 passed, chunking bugs lost content
- v3 (Claude Code with retries): 6/7 passed, AARO still lost pages
- v4 (Anthropic API): 7/7 passed, single-call extraction, no chunking needed

## Remaining issues

- Nimitz executive summary triggers API content filter (military tactical content). Falls back to Claude Code which works but is slower and has 3 missing page annotations.
- Cost tracking shows $0.0000 for API calls because we don't calculate dollar amounts from token counts yet.
