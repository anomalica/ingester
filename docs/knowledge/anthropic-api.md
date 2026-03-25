# Anthropic API for PDF Extraction

Findings from researching optimal PDF extraction via the Claude API.

## Page and token limits

- Maximum 600 pages per request (1M context models)
- Each page costs roughly 2,000-2,500 input tokens (image rendering + text extraction)
- A 100-page document uses ~200k-250k input tokens (~$0.60-$0.75 on Sonnet)
- Maximum request payload: 32 MB. Use the Files API for larger PDFs.
- Dense PDFs can fill context before hitting the page limit

## Pricing (Sonnet 4.6)

| Operation | Cost per million tokens |
|-----------|----------------------|
| Standard input | $3.00 |
| Standard output | $15.00 |
| Batch input | $1.50 |
| Batch output | $7.50 |
| 5-min cache write | $3.75 |
| 1-hour cache write | $6.00 |
| Cache read | $0.30 |
| Batch + cache read | $0.15 |

## Batch API

- 50% discount on all tokens
- Submit up to 100,000 requests per batch (256 MB total)
- Most batches complete in under 1 hour, maximum 24 hours
- Results available for 29 days
- Each batch request is a standard Messages API request - PDFs work
- Batch discount stacks with prompt caching

## Prompt caching

- Cache the extraction prompt so it's only paid once (10% on reads)
- Minimum cacheable size: 2,048 tokens on Sonnet 4.6
- 5-minute cache pays for itself after 1 read
- For multi-document processing: cache write once, all subsequent documents get 90% input savings on the prompt portion
- With batch + cache read: 5% of standard input cost

## Streaming

- Required by the SDK for operations that may exceed 10 minutes
- No effect on output quality, only delivery mechanism
- Use `stream.get_final_message()` to collect the complete response

## Content filtering

- No API parameter to adjust filtering levels
- No academic/research exemption available
- Retrying sometimes helps for false positives
- The Nimitz executive summary consistently triggers the filter (military tactical content)
- Claude Code has more permissive filtering than the raw API

## Optimal strategy

1. Send whole PDFs in one request (most documents are well under 600 pages)
2. Use streaming to avoid SDK timeout on large documents
3. Cache the extraction prompt across requests
4. Use batch API for processing multiple documents
5. Fall back to Claude Code for content-filtered documents
6. No chunking needed unless a document exceeds ~400 pages

## What this means for our chunking

Our current MAX_PAGES_SINGLE_PASS of 20 was set based on Claude Code's limitations (multi-turn tool use overhead, timeouts). With the direct API, the 600-page limit means we can send everything whole. The chunking logic can be simplified to only trigger for very large documents (400+ pages), which we're unlikely to encounter.

## Files API

For large or frequently reused PDFs, upload once via the Files API and reference by file_id. This avoids re-encoding and re-uploading the same PDF. Useful if we need to retry or ask follow-up questions about the same document.

## Provider adapters

Different providers have different capabilities:

| Capability | Claude API | Claude Code | Other models |
|-----------|-----------|-------------|-------------|
| PDF attachment | yes | via Read tool | varies |
| Streaming | yes | n/a | varies |
| Batch API | yes | no | varies |
| Prompt caching | yes | no | varies |
| Content filtering | strict | permissive | varies |
| Max pages | 600 | limited by timeout | varies |
| Turns per extraction | 1 | 6-10 | 1 |
