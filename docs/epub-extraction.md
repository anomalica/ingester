# EPUB extraction

The ebook handler interprets standard EPUB package, navigation and XHTML semantics.
It does not call a model or fetch remote resources. The exporter should preserve
the source EPUB; interpretation belongs here.

## Fidelity rules

- Follow spine order. Resolve resources and navigation targets by package path
  and fragment, never by basename alone.
- Deduplicate image bytes, not image occurrences. Each occurrence retains its
  own alternative text and an explicitly associated printed `figcaption`.
  A single-image `figure` supplies that association; nearby prose does not.
- Keep ambiguous multi-image captions as ordinary source prose, with a warning.
  Captions containing source coordinates or note references also stay in place:
  moving them into an image scalar would move or hide those points.
- Extract ordinary `img` resources and simple SVG image wrappers without
  re-encoding their referenced bytes. Missing/empty resources, external image
  references, composed inline SVG and unsupported embedded objects fail before
  writing an output record. No network fallback or substitute description runs.
- Read EPUB 3 pagebreaks and page-list links, plus EPUB 2 NCX page targets. Keep
  labels exact, including leading zeros and punctuation, using YAML-safe scalar
  quoting when needed. An opaque ID alone is not a printed label; only the narrow
  `page_15`/`page15`/`p15` family is recognised as a label fallback.
- Preserve point adjacency. `photo<pagebreak/>graph` becomes
  `photo<!-- printed_page: 15 -->graph`, not two words. A coincident pagination
  sequence transition is an adjacent `printed_page_sequence` comment.
- Resolve logical chapter boundaries at TOC fragment targets and explicit
  chapter/part markup or Chapter N headings. Nested subsection entries remain
  headings, not invented chapters. Chapter labels are printed numbers, never
  spine ordinals. Ambiguous Roman-numbered titles remain verbatim rather than
  being assumed to identify a part.
- Different TOC labels may legitimately share a target. Interpret that source
  point once, preferring a unique match to its printed opening label or chapter
  designation. If the aliases remain ambiguous, use source headings only rather
  than choosing an arbitrary TOC label. Report the interpretation as a warning;
  retain the complete navigation evidence in the archived EPUB, without adding
  alias fields or duplicate chapters to the Ingest.
- A leading span and its enclosing paragraph can start at the same source point.
  Normalise such targets before splitting to avoid empty leading fragments. At a
  true mid-paragraph boundary, clone the formatting but not the paragraph's
  already-emitted start coordinate.
- Reconcile adjacent designation-only and title sections, including heading-only
  documents, without losing their independent page points. Discard a redundant
  designation's paragraph coordinate only with that designation; never move it
  onto the next paragraph.
- Plan footnote transfers before rendering so notes may precede their references
  in the spine. Remove only transferred definitions with evidenced note semantics.
  Retain residual notes, introductions and meaningful headings. Ordinary internal
  citation links retain their visible words; only evidenced backlinks are removed.
- Rich notes containing media, page points or other unflattenable structure stay
  in their source section with a diagnostic. Their reference remains a footnote
  marker without an inline definition. A rich referenced note outside the readable
  spine fails rather than silently losing it. Missing note targets produce warnings.

The annotation vocabulary is unchanged: `image` (`file`, `alt`, `caption`),
`chapter`, `chapter_title`, `printed_page`, `printed_page_sequence` and the existing
hidden `_kindle_position` point. Strings are YAML scalars, not hand-unescaped text.
Image descriptions are not generated. The existing Arabic pagination-sequence
heuristic is retained; ambiguous nonnumeric pagination runs are not invented.

CSS backgrounds, CSS-generated text and layout reconstruction are not covered by
this deterministic DOM interpretation. Printed captions encoded only through
publisher-specific styling remain source prose, without a guessed association.

## Isolated offline extraction

Use a new output directory, with no canonical records in it. Prepare
`OUTPUT/staging/asset.epub` and `OUTPUT/staging/manifest.json`:

```json
{
  "asset": "asset.epub",
  "source": "exported-book.epub",
  "detected_type": "application/epub+zip",
  "fetch_method": "local",
  "fetched_at": "2026-09-27T00:00:00Z"
}
```

Use the actual acquisition timestamp, not the example. `asset_hash` is an optional
full SHA-256 checked by finalisation. Add an evidenced `source_url` only when known.
The ebook handler defaults copyright to `licensed`; a staging `copyright_status`
does not override that default.

From `formats/ebook/`, with `OUTPUT` set to the absolute output directory:

```bash
cm run -- --network none -e PYTHONDONTWRITEBYTECODE=1 \
  -e "INGESTER_VERSION=$VERSION" -- \
  ingest "output=$OUTPUT" /mnt/output/staging
```

Set `VERSION` from the Ingester revision; identify an uncommitted test build as
dirty. The first `--` preserves the runtime separator through the installed
container-magic CLI. The command uses the existing development image and mounted
production handler code; no build or dependency download is needed.

The handler creates a run-private `record/1` intermediate named by the EPUB's
Asset hash. It is not the final record. Follow the ordinary host pipeline's local
steps, with every destination inside the isolated directory:

1. `shared/archive.py --source ... --target OUTPUT/records/ASSET_HASH.epub
   --expected-hash ASSET_HASH`
2. `shared/record3.py finalise --record OUTPUT/store/ASSET_HASH.md --asset ...
   --manifest ... --output-dir OUTPUT`, with `anomalica-common/src` on `PYTHONPATH`.
   This returns the final repository-relative record path and moves media and
   aliases to its Selection-derived identity.
3. `shared/verification.py --record FINAL_RECORD --source STAGED_EPUB`
4. `shared/quality.py stamp FINAL_RECORD`
5. `validator.validate(text, expected_schema="anomalica/record/3")` and independent
   checks of every media filename, hash and annotation.

Do not use the top-level `./ingest --no-commit` as an isolation mechanism: it still
targets the canonical store and performs the archive/backup/publication workflow.
The isolated path creates no committed-ingest receipt and publishes nothing.
Legacy verification sidecars mirror existing producer behaviour, not current
proof-of-possession authority.

This procedure verifies fresh outputs, not refresh carry-over from older ingests.
The existing cross-format `shared/refresh.py` pairs images by alt/caption text. A
newly recovered caption can leave an older annotation unmatched and append it as a
duplicate. Do not use a bulk forced refresh to accept this extraction change;
review that carry-over policy separately before regenerating canonical records.

## Verification

From `formats/ebook/`:

```bash
cm run -- --network none -e PYTHONDONTWRITEBYTECODE=1 -- \
  test -q -p no:cacheprovider
```

`workspace/tests/test_epub_fidelity.py` supplies standard EPUB ZIP fixtures for
occurrence metadata, byte preservation, failure-before-write, page-list/NCX points,
inline adjacency, logical chapter boundaries and residual note text. It also runs
the real handler and Record finaliser against a temporary isolated bundle.

For fresh books, compare image occurrences separately from unique media counts;
compare exact page labels and neighbouring source text; inspect every chapter
opening, ending and note section. Record validation alone does not prove fidelity.
The shared pre-digest must parse caption scalars as YAML and remove inline point
comments without adding whitespace. Its consumer tests belong to Product/Common.
