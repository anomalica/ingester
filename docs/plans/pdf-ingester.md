# PDF Ingester Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a CLI tool that extracts structured content from PDFs using a vision model and outputs DoclingDocument JSON files.

**Architecture:** A CLI entry point reads a PDF, sends it to Claude Code (headless) via a provider abstraction, receives structured JSON back, maps it to a DoclingDocument using docling-core's builder API, and writes the result as JSON. Large documents are split into chunks.

**Tech Stack:** Python 3.10+, docling-core, pikepdf, Claude Code (headless), container-magic

**Spec:** `docs/specs/pdf-ingester-design.md`

**Commit convention:** Stage changes with `git add`, then wait for user to test before committing. Commit steps in the tasks below show the command, but do not run them until the user has reviewed and approved.

---

## File Structure

```
pdf/
  cm.yaml                          # container-magic config
  workspace/
    ingest_pdf.py                  # CLI entry point (argparse)
    extraction/
      __init__.py
      models.py                    # ElementItem, ExtractionResult Pydantic models
      prompt.py                    # extraction prompt template
      provider.py                  # ExtractionProvider base class
      claude_code.py               # Claude Code headless implementation
      chunker.py                   # PDF splitting and chunk management
      merger.py                    # page-boundary merging heuristics
    output/
      __init__.py
      builder.py                   # ExtractionResult to DoclingDocument mapping
    tests/
      __init__.py
      test_models.py               # model validation tests
      test_prompt.py               # prompt construction tests
      test_builder.py              # DoclingDocument builder tests
      test_merger.py               # merging heuristic tests
      test_chunker.py              # PDF splitting tests
      test_ingest_pdf.py           # end-to-end CLI tests
      fixtures/
        simple.pdf                 # 1-page born-digital PDF for testing
        multipage.pdf              # 3-page PDF for chunking tests
  justfile                         # just ingest-pdf <file> [output-dir]
```

---

### Task 1: Container-magic setup and Python project skeleton

**Files:**
- Create: `pdf/cm.yaml`
- Create: `pdf/workspace/requirements.txt`
- Create: `pdf/justfile`

- [ ] **Step 1: Create cm.yaml**

```yaml
names:
  image: anomalica-ingester-pdf
  workspace: workspace
  user: nonroot

runtime:
  features: []

stages:
  base:
    from: python:3.12-slim
    steps:
      - apt-get:
          install:
            - curl
      - pip:
          install:
            - docling-core
            - pikepdf
            - pydantic

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
```

- [ ] **Step 2: Create requirements.txt**

```
docling-core
pikepdf
pydantic
```

- [ ] **Step 3: Create justfile**

```just
ingest-pdf *ARGS:
    python workspace/ingest_pdf.py {{ARGS}}

test *ARGS:
    pytest workspace/tests/ {{ARGS}} -v
```

- [ ] **Step 4: Run cm update and cm build**

```bash
cd pdf && cm update && cm build
```

Expected: Docker image builds successfully.

- [ ] **Step 5: Verify Python and dependencies**

```bash
cd pdf && cm run python -c "import docling_core; import pikepdf; print('OK')"
```

Expected: Prints `OK`.

- [ ] **Step 6: Commit**

```bash
git add pdf/
git commit -m "feat(pdf): scaffold container-magic project with dependencies"
```

---

### Task 2: Pydantic models (ElementItem, ExtractionResult)

**Files:**
- Create: `pdf/workspace/extraction/__init__.py`
- Create: `pdf/workspace/extraction/models.py`
- Create: `pdf/workspace/tests/__init__.py`
- Create: `pdf/workspace/tests/test_models.py`

- [ ] **Step 1: Write tests for models**

```python
# pdf/workspace/tests/test_models.py
from extraction.models import ElementItem, ExtractionResult


def test_paragraph_element():
    el = ElementItem(type="paragraph", text="Hello world.", page=1)
    assert el.type == "paragraph"
    assert el.page_end is None


def test_heading_element_with_level():
    el = ElementItem(type="heading", text="Introduction", page=1, level=2)
    assert el.level == 2


def test_table_element_with_rows():
    el = ElementItem(
        type="table",
        text="",
        page=3,
        page_end=4,
        caption="Table 1",
        rows=[["Year", "Count"], ["2020", "42"]],
    )
    assert len(el.rows) == 2
    assert el.page_end == 4


def test_redacted_element():
    el = ElementItem(type="redacted", text="", page=6, extent="paragraph")
    assert el.extent == "paragraph"


def test_extraction_result():
    result = ExtractionResult(
        metadata={"title": "Test", "authors": ["Author"], "date": "2021-01-01"},
        elements=[
            ElementItem(type="paragraph", text="Content.", page=1),
        ],
    )
    assert result.metadata["title"] == "Test"
    assert len(result.elements) == 1


def test_extraction_result_from_json():
    raw = {
        "metadata": {"title": "Test", "authors": [], "date": "2021-01-01"},
        "elements": [
            {"type": "heading", "level": 1, "text": "Title", "page": 1},
            {"type": "paragraph", "text": "Body.", "page": 1},
        ],
    }
    result = ExtractionResult.model_validate(raw)
    assert result.elements[0].level == 1
    assert result.elements[1].level is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd pdf && cm run pytest workspace/tests/test_models.py -v
```

Expected: ImportError - extraction.models not found.

- [ ] **Step 3: Implement models**

```python
# pdf/workspace/extraction/__init__.py
```

```python
# pdf/workspace/extraction/models.py
from __future__ import annotations

from pydantic import BaseModel


class ElementItem(BaseModel):
    type: str
    text: str
    page: int
    page_end: int | None = None
    level: int | None = None
    caption: str | None = None
    rows: list[list[str]] | None = None
    extent: str | None = None


class ExtractionResult(BaseModel):
    metadata: dict
    elements: list[ElementItem]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd pdf && cm run pytest workspace/tests/test_models.py -v
```

Expected: All 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add pdf/workspace/extraction/ pdf/workspace/tests/
git commit -m "feat(pdf): add extraction Pydantic models"
```

---

### Task 3: Extraction prompt template

**Files:**
- Create: `pdf/workspace/extraction/prompt.py`
- Create: `pdf/workspace/tests/test_prompt.py`

- [ ] **Step 1: Write tests for prompt construction**

```python
# pdf/workspace/tests/test_prompt.py
from extraction.prompt import build_extraction_prompt


def test_prompt_contains_schema():
    prompt = build_extraction_prompt()
    assert '"type"' in prompt
    assert '"page"' in prompt
    assert "metadata" in prompt


def test_prompt_mentions_redaction():
    prompt = build_extraction_prompt()
    assert "[REDACTED]" in prompt


def test_prompt_mentions_image_description():
    prompt = build_extraction_prompt()
    assert "image" in prompt.lower()
    assert "description" in prompt.lower()


def test_prompt_with_page_offset():
    prompt = build_extraction_prompt(page_offset=51, page_count=50)
    assert "51" in prompt
    assert "100" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd pdf && cm run pytest workspace/tests/test_prompt.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement prompt template**

```python
# pdf/workspace/extraction/prompt.py
from __future__ import annotations

EXTRACTION_SCHEMA = """{
  "metadata": {
    "title": "string",
    "authors": ["string"],
    "date": "string or null"
  },
  "elements": [
    {
      "type": "heading | paragraph | table | list_item | image_description | redacted",
      "text": "string",
      "page": "integer",
      "page_end": "integer or null (for multi-page elements)",
      "level": "integer or null (heading level, 1-6)",
      "caption": "string or null (table caption)",
      "rows": "[[string]] or null (table rows including header row)",
      "extent": "string or null (for redacted: a few words | sentence | paragraph | page)"
    }
  ]
}"""


def build_extraction_prompt(
    page_offset: int | None = None, page_count: int | None = None
) -> str:
    page_context = ""
    if page_offset is not None and page_count is not None:
        page_end = page_offset + page_count - 1
        page_context = (
            f"\n\nThese are pages {page_offset} to {page_end} of a larger document. "
            f"Number pages starting from {page_offset}."
        )

    return f"""Extract all content from this PDF into structured JSON.

Rules:
- Preserve document structure: headings (with hierarchy level 1-6), paragraphs, lists, tables
- Skip page furniture: page numbers, headers, footers, watermarks
- Tables: extract as structured data with rows and columns, not flattened text. Include a header row.
- Images, figures, diagrams: do not extract the image. Instead, provide a factual description of what is depicted, using type "image_description".
- Redacted sections: mark with type "redacted", text "[REDACTED]", and estimate the extent (a few words, sentence, paragraph, or page).
- Illegible text: use "[illegible]" or "[partially illegible: best guess here]" in the text field.
- Record the page number for every element.
- For elements spanning multiple pages, set page to where it starts and page_end to where it ends.{page_context}

Return ONLY valid JSON matching this schema:

{EXTRACTION_SCHEMA}"""
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd pdf && cm run pytest workspace/tests/test_prompt.py -v
```

Expected: All 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add pdf/workspace/extraction/prompt.py pdf/workspace/tests/test_prompt.py
git commit -m "feat(pdf): add extraction prompt template"
```

---

### Task 4: Provider base class and Claude Code headless implementation

**Files:**
- Create: `pdf/workspace/extraction/provider.py`
- Create: `pdf/workspace/extraction/claude_code.py`

The Claude Code provider shells out to `claude --print --json-schema '...' --model sonnet` with the PDF content and extraction prompt. Since this calls an external process, we test it with a manual integration test rather than unit tests.

- [ ] **Step 1: Implement provider base class**

```python
# pdf/workspace/extraction/provider.py
from __future__ import annotations

from abc import ABC, abstractmethod

from extraction.models import ExtractionResult


class ExtractionProvider(ABC):
    @abstractmethod
    def extract(self, pdf_data: bytes) -> ExtractionResult:
        """Send entire PDF to model, return structured extraction."""
        ...

    @abstractmethod
    def extract_chunk(self, pdf_data: bytes, page_offset: int, page_count: int) -> ExtractionResult:
        """Send a chunk of pages to model.
        page_offset: 1-based page number of first page in this chunk.
        page_count: number of pages in this chunk."""
        ...
```

- [ ] **Step 2: Implement Claude Code headless provider**

```python
# pdf/workspace/extraction/claude_code.py
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from extraction.models import ExtractionResult
from extraction.prompt import build_extraction_prompt
from extraction.provider import ExtractionProvider


class ClaudeCodeProvider(ExtractionProvider):
    def __init__(self, model: str = "sonnet"):
        self.model = model

    def _call_claude(self, prompt: str, pdf_path: Path) -> ExtractionResult:
        schema = json.dumps(ExtractionResult.model_json_schema())
        full_prompt = f"{prompt}\n\nThe PDF file to extract is: {pdf_path}"

        result = subprocess.run(
            ["claude", "--print",
             "--model", self.model,
             "--json-schema", schema,
             "--allowedTools", "Read",
             "--no-session-persistence",
             "--add-dir", str(pdf_path.parent),
             full_prompt],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Claude Code failed (exit {result.returncode}): {result.stderr}"
            )

        return ExtractionResult.model_validate_json(result.stdout)

    def extract(self, pdf_data: bytes) -> ExtractionResult:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_data)
            pdf_path = Path(f.name)
        try:
            prompt = build_extraction_prompt()
            return self._call_claude(prompt, pdf_path)
        finally:
            pdf_path.unlink(missing_ok=True)

    def extract_chunk(
        self, pdf_data: bytes, page_offset: int, page_count: int
    ) -> ExtractionResult:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_data)
            pdf_path = Path(f.name)
        try:
            prompt = build_extraction_prompt(
                page_offset=page_offset, page_count=page_count
            )
            return self._call_claude(prompt, pdf_path)
        finally:
            pdf_path.unlink(missing_ok=True)
```

Note: The exact invocation of Claude Code with a PDF may need adjustment during implementation. The approach above gives Claude Code Read tool access (`--allowedTools Read`) and `--add-dir` access to the PDF's directory, then references the file path in the prompt. Claude Code should then use its Read tool to read the PDF. If this does not work, alternatives include piping via stdin or using the `--file` flag. Verify against actual CLI behaviour during Task 4.

- [ ] **Step 3: Commit**

```bash
git add pdf/workspace/extraction/provider.py pdf/workspace/extraction/claude_code.py
git commit -m "feat(pdf): add provider base class and Claude Code implementation"
```

---

### Task 5: PDF chunking (split and page counting)

**Files:**
- Create: `pdf/workspace/extraction/chunker.py`
- Create: `pdf/workspace/tests/test_chunker.py`
- Create: `pdf/workspace/tests/fixtures/` (test PDFs)

- [ ] **Step 1: Create test fixture PDFs**

```python
# Run once to generate test fixtures. pikepdf can create minimal PDFs.
# pdf/workspace/tests/create_fixtures.py
import pikepdf

def create_simple_pdf(path, num_pages=1):
    """Create a minimal PDF with the given number of pages."""
    pdf = pikepdf.Pdf.new()
    for i in range(num_pages):
        page = pikepdf.Page(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Page,
                MediaBox=[0, 0, 612, 792],
            )
        )
        pdf.pages.append(page)
    pdf.save(path)

if __name__ == "__main__":
    import pathlib
    fixtures = pathlib.Path(__file__).parent / "fixtures"
    fixtures.mkdir(exist_ok=True)
    create_simple_pdf(fixtures / "simple.pdf", 1)
    create_simple_pdf(fixtures / "multipage.pdf", 3)
    create_simple_pdf(fixtures / "large.pdf", 120)
    print("Fixtures created.")
```

```bash
cd pdf && cm run python workspace/tests/create_fixtures.py
```

- [ ] **Step 2: Write tests for chunker**

```python
# pdf/workspace/tests/test_chunker.py
from pathlib import Path

from extraction.chunker import get_page_count, split_pdf

FIXTURES = Path(__file__).parent / "fixtures"


def test_get_page_count_simple():
    assert get_page_count(FIXTURES / "simple.pdf") == 1


def test_get_page_count_multipage():
    assert get_page_count(FIXTURES / "multipage.pdf") == 3


def test_get_page_count_large():
    assert get_page_count(FIXTURES / "large.pdf") == 120


def test_split_pdf_no_split_needed():
    chunks = split_pdf(FIXTURES / "multipage.pdf", max_pages=50)
    assert len(chunks) == 1
    assert chunks[0]["page_offset"] == 1
    assert chunks[0]["page_count"] == 3


def test_split_pdf_into_chunks():
    chunks = split_pdf(FIXTURES / "large.pdf", max_pages=50)
    assert len(chunks) == 3  # 50 + 50 + 20
    assert chunks[0]["page_offset"] == 1
    assert chunks[0]["page_count"] == 50
    assert chunks[1]["page_offset"] == 51
    assert chunks[1]["page_count"] == 50
    assert chunks[2]["page_offset"] == 101
    assert chunks[2]["page_count"] == 20


def test_split_pdf_chunk_data_is_valid_pdf():
    chunks = split_pdf(FIXTURES / "multipage.pdf", max_pages=2)
    assert len(chunks) == 2
    # Each chunk's pdf_data should be loadable
    import io
    import pikepdf
    for chunk in chunks:
        pdf = pikepdf.Pdf.open(io.BytesIO(chunk["pdf_data"]))
        assert len(pdf.pages) == chunk["page_count"]
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd pdf && cm run pytest workspace/tests/test_chunker.py -v
```

Expected: ImportError.

- [ ] **Step 4: Implement chunker**

```python
# pdf/workspace/extraction/chunker.py
from __future__ import annotations

import io
from pathlib import Path

import pikepdf


def get_page_count(pdf_path: Path) -> int:
    with pikepdf.Pdf.open(pdf_path) as pdf:
        return len(pdf.pages)


def split_pdf(pdf_path: Path, max_pages: int = 50) -> list[dict]:
    """Split a PDF into chunks of at most max_pages pages.

    Returns a list of dicts, each with:
      - pdf_data: bytes of the chunk PDF
      - page_offset: 1-based page number of the first page
      - page_count: number of pages in this chunk
    """
    with pikepdf.Pdf.open(pdf_path) as pdf:
        total = len(pdf.pages)
        if total <= max_pages:
            return [
                {
                    "pdf_data": pdf_path.read_bytes(),
                    "page_offset": 1,
                    "page_count": total,
                }
            ]

        chunks = []
        for start in range(0, total, max_pages):
            end = min(start + max_pages, total)
            chunk_pdf = pikepdf.Pdf.new()
            for page_idx in range(start, end):
                chunk_pdf.pages.append(pdf.pages[page_idx])
            buf = io.BytesIO()
            chunk_pdf.save(buf)
            chunks.append(
                {
                    "pdf_data": buf.getvalue(),
                    "page_offset": start + 1,
                    "page_count": end - start,
                }
            )
        return chunks
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd pdf && cm run pytest workspace/tests/test_chunker.py -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add pdf/workspace/extraction/chunker.py pdf/workspace/tests/
git commit -m "feat(pdf): add PDF chunking and page counting"
```

---

### Task 6: DoclingDocument builder

**Files:**
- Create: `pdf/workspace/output/__init__.py`
- Create: `pdf/workspace/output/builder.py`
- Create: `pdf/workspace/tests/test_builder.py`

- [ ] **Step 1: Write tests for builder**

```python
# pdf/workspace/tests/test_builder.py
import json

from extraction.models import ElementItem, ExtractionResult
from output.builder import build_docling_document


def _make_result(*elements, title="Test", authors=None, date="2021-01-01"):
    return ExtractionResult(
        metadata={"title": title, "authors": authors or [], "date": date},
        elements=[ElementItem(**el) for el in elements],
    )


def test_empty_document():
    result = _make_result()
    doc = build_docling_document(result, source_filename="empty.pdf")
    assert doc.name == "empty.pdf"


def test_paragraph():
    result = _make_result({"type": "paragraph", "text": "Hello world.", "page": 1})
    doc = build_docling_document(result, source_filename="test.pdf")
    exported = doc.export_to_dict()
    assert len(exported["texts"]) >= 1
    texts = [t["text"] for t in exported["texts"]]
    assert "Hello world." in texts


def test_heading():
    result = _make_result(
        {"type": "heading", "text": "Introduction", "page": 1, "level": 1}
    )
    doc = build_docling_document(result, source_filename="test.pdf")
    exported = doc.export_to_dict()
    # add_heading creates a section_header text item
    heading_texts = [
        t["text"] for t in exported["texts"]
        if "header" in t.get("label", "").lower()
    ]
    assert "Introduction" in heading_texts


def test_table():
    result = _make_result(
        {
            "type": "table",
            "text": "",
            "page": 2,
            "caption": "Table 1",
            "rows": [["Name", "Value"], ["A", "1"]],
        }
    )
    doc = build_docling_document(result, source_filename="test.pdf")
    exported = doc.export_to_dict()
    assert len(exported["tables"]) == 1


def test_image_description():
    result = _make_result(
        {"type": "image_description", "text": "A bar chart showing growth.", "page": 3}
    )
    doc = build_docling_document(result, source_filename="test.pdf")
    exported = doc.export_to_dict()
    texts = [t["text"] for t in exported["texts"]]
    assert "A bar chart showing growth." in texts


def test_roundtrip_json():
    result = _make_result(
        {"type": "heading", "text": "Title", "page": 1, "level": 1},
        {"type": "paragraph", "text": "Body text.", "page": 1},
    )
    doc = build_docling_document(result, source_filename="test.pdf")
    json_str = json.dumps(doc.export_to_dict())
    assert "Title" in json_str
    assert "Body text." in json_str
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd pdf && cm run pytest workspace/tests/test_builder.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement builder**

This is where we need to explore docling-core's builder API. The implementation will use `DoclingDocument(name=...)` and its `add_text`, `add_heading`, `add_table` etc. methods. The exact API may differ from what's shown here - consult docling-core's source/docs during implementation.

```python
# pdf/workspace/output/__init__.py
```

```python
# pdf/workspace/output/builder.py
from __future__ import annotations

from docling_core.types.doc import DoclingDocument, DocItemLabel

from extraction.models import ExtractionResult


def build_docling_document(
    result: ExtractionResult, source_filename: str
) -> DoclingDocument:
    doc = DoclingDocument(name=source_filename)

    for el in result.elements:
        if el.type == "heading":
            doc.add_heading(text=el.text, level=el.level or 1)
        elif el.type == "paragraph":
            doc.add_text(label=DocItemLabel.PARAGRAPH, text=el.text)
        elif el.type == "list_item":
            doc.add_list_item(text=el.text)
        elif el.type == "table":
            _add_table(doc, el)
        elif el.type == "image_description":
            # Store as paragraph; docling-core has PictureItem with
            # DescriptionAnnotation for richer semantics, but for now
            # a labelled paragraph preserves the text for the digester.
            doc.add_text(label=DocItemLabel.PARAGRAPH, text=el.text)
        elif el.type == "redacted":
            doc.add_text(label=DocItemLabel.PARAGRAPH, text=el.text or "[REDACTED]")

    return doc


def _add_table(doc: DoclingDocument, el) -> None:
    """Add a table element to the document.

    docling-core's table API involves constructing TableData with TableCell
    objects. Verify the exact constructor signatures during implementation.
    """
    from docling_core.types.doc import TableCell, TableData

    if not el.rows:
        return

    table_cells = []
    for row_idx, row in enumerate(el.rows):
        for col_idx, cell_text in enumerate(row):
            table_cells.append(
                TableCell(
                    text=cell_text,
                    row_span=1,
                    col_span=1,
                    start_row_offset_idx=row_idx,
                    end_row_offset_idx=row_idx + 1,
                    start_col_offset_idx=col_idx,
                    end_col_offset_idx=col_idx + 1,
                    column_header=row_idx == 0,
                )
            )

    table_data = TableData(
        table_cells=table_cells,
        num_rows=len(el.rows),
        num_cols=len(el.rows[0]) if el.rows else 0,
    )
    doc.add_table(data=table_data)
```

Note: The docling-core builder API for tables and provenance (page numbers) needs to be verified during implementation. The method signatures shown are best-effort based on research but may need adjustment.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd pdf && cm run pytest workspace/tests/test_builder.py -v
```

Expected: All 6 tests pass. Some may need adjustment based on the actual docling-core API.

- [ ] **Step 5: Commit**

```bash
git add pdf/workspace/output/ pdf/workspace/tests/test_builder.py
git commit -m "feat(pdf): add DoclingDocument builder"
```

---

### Task 7: Page-boundary merger

**Files:**
- Create: `pdf/workspace/extraction/merger.py`
- Create: `pdf/workspace/tests/test_merger.py`

- [ ] **Step 1: Write tests for merger**

```python
# pdf/workspace/tests/test_merger.py
from extraction.merger import merge_extraction_results
from extraction.models import ElementItem, ExtractionResult


def _result(*elements):
    return ExtractionResult(
        metadata={"title": "Test", "authors": [], "date": "2021-01-01"},
        elements=[ElementItem(**el) for el in elements],
    )


def test_merge_single_result():
    r = _result({"type": "paragraph", "text": "Hello.", "page": 1})
    merged = merge_extraction_results([r])
    assert len(merged.elements) == 1


def test_merge_joins_split_paragraph():
    r1 = _result({"type": "paragraph", "text": "The quick brown fox", "page": 1})
    r2 = _result({"type": "paragraph", "text": "jumped over the lazy dog.", "page": 2})
    merged = merge_extraction_results([r1, r2])
    assert len(merged.elements) == 1
    assert "fox jumped" in merged.elements[0].text


def test_merge_does_not_join_complete_paragraphs():
    r1 = _result({"type": "paragraph", "text": "First paragraph.", "page": 1})
    r2 = _result({"type": "paragraph", "text": "Second paragraph.", "page": 2})
    merged = merge_extraction_results([r1, r2])
    assert len(merged.elements) == 2


def test_merge_preserves_headings():
    r1 = _result(
        {"type": "heading", "text": "Chapter 1", "page": 1, "level": 1},
        {"type": "paragraph", "text": "Content.", "page": 1},
    )
    r2 = _result(
        {"type": "paragraph", "text": "More content.", "page": 2},
    )
    merged = merge_extraction_results([r1, r2])
    assert merged.elements[0].type == "heading"


def test_merge_empty_list():
    merged = merge_extraction_results([])
    assert merged.elements == []


def test_merge_tables_with_matching_columns():
    r1 = _result(
        {"type": "table", "text": "", "page": 1,
         "rows": [["Name", "Value"], ["A", "1"]]}
    )
    r2 = _result(
        {"type": "table", "text": "", "page": 2,
         "rows": [["Name", "Value"], ["B", "2"]]}
    )
    merged = merge_extraction_results([r1, r2])
    assert len(merged.elements) == 1
    assert len(merged.elements[0].rows) == 3  # header + 2 data rows


def test_merge_three_chunks():
    r1 = _result({"type": "paragraph", "text": "The quick brown", "page": 1})
    r2 = _result({"type": "paragraph", "text": "fox jumped over", "page": 2})
    r3 = _result({"type": "paragraph", "text": "the lazy dog.", "page": 3})
    merged = merge_extraction_results([r1, r2, r3])
    assert len(merged.elements) == 1
    assert "quick brown fox jumped over the lazy dog" in merged.elements[0].text


def test_merge_uses_first_result_metadata():
    r1 = ExtractionResult(
        metadata={"title": "Real Title", "authors": ["A"], "date": "2021-01-01"},
        elements=[],
    )
    r2 = ExtractionResult(
        metadata={"title": "", "authors": [], "date": ""},
        elements=[],
    )
    merged = merge_extraction_results([r1, r2])
    assert merged.metadata["title"] == "Real Title"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd pdf && cm run pytest workspace/tests/test_merger.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement merger**

```python
# pdf/workspace/extraction/merger.py
from __future__ import annotations

from extraction.models import ElementItem, ExtractionResult


def _looks_incomplete(text: str) -> bool:
    """Check if text looks like it was cut off mid-sentence."""
    stripped = text.rstrip()
    if not stripped:
        return False
    return stripped[-1] not in ".!?:;\"')"


def _looks_like_continuation(text: str) -> bool:
    """Check if text looks like it continues from a previous element."""
    stripped = text.lstrip()
    if not stripped:
        return False
    return stripped[0].islower()


def merge_extraction_results(results: list[ExtractionResult]) -> ExtractionResult:
    if not results:
        return ExtractionResult(metadata={}, elements=[])
    if len(results) == 1:
        return results[0]

    metadata = results[0].metadata
    merged_elements: list[ElementItem] = []

    for result in results:
        for el in result.elements:
            if (
                merged_elements
                and merged_elements[-1].type == "paragraph"
                and el.type == "paragraph"
                and _looks_incomplete(merged_elements[-1].text)
                and _looks_like_continuation(el.text)
            ):
                # Join paragraphs split across chunk boundaries
                prev = merged_elements[-1]
                merged_elements[-1] = ElementItem(
                    type="paragraph",
                    text=prev.text.rstrip() + " " + el.text.lstrip(),
                    page=prev.page,
                    page_end=el.page_end or el.page,
                )
            elif (
                merged_elements
                and merged_elements[-1].type == "table"
                and el.type == "table"
                and merged_elements[-1].rows
                and el.rows
                and len(merged_elements[-1].rows[0]) == len(el.rows[0])
            ):
                # Merge tables with matching column structure across chunks.
                # Skip the header row of the second table if it matches.
                prev = merged_elements[-1]
                new_rows = el.rows
                if prev.rows[0] == el.rows[0]:
                    new_rows = el.rows[1:]
                merged_elements[-1] = ElementItem(
                    type="table",
                    text=prev.text,
                    page=prev.page,
                    page_end=el.page_end or el.page,
                    caption=prev.caption,
                    rows=prev.rows + new_rows,
                )
            else:
                merged_elements.append(el)

    return ExtractionResult(metadata=metadata, elements=merged_elements)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd pdf && cm run pytest workspace/tests/test_merger.py -v
```

Expected: All 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add pdf/workspace/extraction/merger.py pdf/workspace/tests/test_merger.py
git commit -m "feat(pdf): add page-boundary merging heuristics"
```

---

### Task 8: CLI entry point

**Files:**
- Create: `pdf/workspace/ingest_pdf.py`
- Create: `pdf/workspace/tests/test_ingest_pdf.py`

- [ ] **Step 1: Write integration test for CLI**

```python
# pdf/workspace/tests/test_ingest_pdf.py
import json
import subprocess
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def test_cli_missing_input_file():
    result = subprocess.run(
        ["python", "workspace/ingest_pdf.py"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


def test_cli_nonexistent_file():
    result = subprocess.run(
        ["python", "workspace/ingest_pdf.py", "/nonexistent.pdf"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "not found" in result.stderr.lower() or "error" in result.stderr.lower()


def test_cli_skips_existing_output(tmp_path):
    output_file = tmp_path / "simple.json"
    output_file.write_text("{}")
    result = subprocess.run(
        ["python", "workspace/ingest_pdf.py",
         str(FIXTURES / "simple.pdf"),
         str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "skip" in result.stderr.lower()
    assert output_file.read_text() == "{}"  # unchanged


def test_cli_force_flag(tmp_path):
    """This test requires Claude Code to be available.
    Skip in CI or when claude is not installed."""
    import shutil
    if not shutil.which("claude"):
        import pytest
        pytest.skip("Claude Code not available")

    # This is a full integration test - it will make a real API call.
    # Only run manually.
    import pytest
    pytest.skip("Integration test - run manually with: pytest -k test_cli_force_flag --run-integration")
```

- [ ] **Step 2: Implement CLI**

```python
#!/usr/bin/env python3
# pdf/workspace/ingest_pdf.py
"""PDF ingester - extracts structured content from PDFs into DoclingDocument JSON."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from extraction.chunker import get_page_count, split_pdf
from extraction.claude_code import ClaudeCodeProvider
from extraction.merger import merge_extraction_results
from output.builder import build_docling_document

MAX_PAGES_SINGLE_PASS = 100
CHUNK_SIZE = 50
MIN_CHUNK_SIZE = 5


def main():
    parser = argparse.ArgumentParser(description="Extract content from a PDF into DoclingDocument JSON.")
    parser.add_argument("input_file", type=Path, help="Path to the PDF file")
    parser.add_argument("output_dir", type=Path, nargs="?", default=Path("."), help="Output directory (default: current directory)")
    parser.add_argument("--force", action="store_true", help="Re-process even if output file exists")
    args = parser.parse_args()

    if not args.input_file.exists():
        print(f"Error: file not found: {args.input_file}", file=sys.stderr)
        sys.exit(1)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_file = args.output_dir / f"{args.input_file.stem}.json"

    if output_file.exists() and not args.force:
        print(f"Skipping: {output_file} already exists (use --force to re-process)", file=sys.stderr)
        sys.exit(0)

    provider = ClaudeCodeProvider()
    pdf_data = args.input_file.read_bytes()
    page_count = get_page_count(args.input_file)
    print(f"Processing: {args.input_file} ({page_count} pages)", file=sys.stderr)

    if page_count <= MAX_PAGES_SINGLE_PASS:
        try:
            result = provider.extract(pdf_data)
            results = [result]
        except RuntimeError:
            print("Single-pass failed, falling back to chunked extraction", file=sys.stderr)
            results = _extract_chunked(provider, args.input_file, CHUNK_SIZE)
    else:
        results = _extract_chunked(provider, args.input_file, CHUNK_SIZE)

    merged = merge_extraction_results(results)

    # Validate page coverage
    extracted_pages = set()
    for el in merged.elements:
        extracted_pages.add(el.page)
        if el.page_end:
            for p in range(el.page, el.page_end + 1):
                extracted_pages.add(p)
    missing = set(range(1, page_count + 1)) - extracted_pages
    if missing:
        print(f"Warning: pages not referenced in extraction: {sorted(missing)}", file=sys.stderr)

    doc = build_docling_document(merged, source_filename=args.input_file.name)
    output_file.write_text(json.dumps(doc.export_to_dict(), indent=2))
    print(f"Written: {output_file}", file=sys.stderr)


def _extract_chunked(provider, pdf_path: Path, chunk_size: int) -> list:
    chunks = split_pdf(pdf_path, max_pages=chunk_size)
    results = []
    for i, chunk in enumerate(chunks, 1):
        page_end = chunk["page_offset"] + chunk["page_count"] - 1
        print(
            f"Processing chunk {i}/{len(chunks)}, "
            f"pages {chunk['page_offset']}-{page_end}",
            file=sys.stderr,
        )
        try:
            result = provider.extract_chunk(
                chunk["pdf_data"], chunk["page_offset"], chunk["page_count"]
            )
            results.append(result)
        except RuntimeError:
            if chunk_size <= MIN_CHUNK_SIZE:
                print(
                    f"Error: chunk starting at page {chunk['page_offset']} "
                    f"failed at minimum chunk size ({MIN_CHUNK_SIZE} pages). "
                    f"Flagging for manual intervention.",
                    file=sys.stderr,
                )
                sys.exit(1)
            # Re-split only the failed chunk with a smaller size, not the whole document
            smaller = chunk_size // 2
            print(
                f"Chunk {i} failed, re-splitting pages {chunk['page_offset']}-{page_end} "
                f"into {smaller}-page chunks",
                file=sys.stderr,
            )
            # Write the failed chunk to a temp file and re-split it
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(chunk["pdf_data"])
                chunk_path = Path(f.name)
            try:
                sub_results = _extract_chunked(provider, chunk_path, smaller)
                # Adjust page offsets since the sub-chunks start from page 1
                # but should be relative to the original document
                for sub_result in sub_results:
                    for el in sub_result.elements:
                        el.page += chunk["page_offset"] - 1
                        if el.page_end:
                            el.page_end += chunk["page_offset"] - 1
                results.extend(sub_results)
            finally:
                chunk_path.unlink(missing_ok=True)
    return results


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run unit tests**

```bash
cd pdf && cm run pytest workspace/tests/test_ingest_pdf.py -v -k "not integration"
```

Expected: CLI argument tests pass.

- [ ] **Step 4: Manual integration test with a real PDF**

Download a test PDF from the test corpus and run:

```bash
cd pdf && cm run python workspace/ingest_pdf.py /path/to/test.pdf output/
```

Inspect `output/test.json` to verify the extraction looks correct.

- [ ] **Step 5: Commit**

```bash
git add pdf/workspace/ingest_pdf.py pdf/workspace/tests/test_ingest_pdf.py
git commit -m "feat(pdf): add CLI entry point with chunking and idempotency"
```

---

### Task 9: End-to-end verification with test corpus PDF

This is a manual verification step using a real PDF from the test corpus.

- [ ] **Step 1: Download a test corpus PDF**

The test corpus in the meta-repo lists several PDFs. Pick one that's publicly available (e.g. the ODNI UAP Preliminary Assessment, which is a short government document).

- [ ] **Step 2: Run the ingester**

```bash
cd pdf && cm run python workspace/ingest_pdf.py /path/to/uap-preliminary-assessment.pdf output/
```

- [ ] **Step 3: Inspect the output**

Check `output/uap-preliminary-assessment.json`:
- Does it have the correct title in metadata?
- Are headings extracted with correct hierarchy?
- Is body text intact (no page numbers mid-sentence)?
- Are any tables extracted as structured data?
- Is the page count plausible?

- [ ] **Step 4: Try the markdown export for human inspection**

```python
from docling_core.types.doc import DoclingDocument
import json

with open("output/uap-preliminary-assessment.json") as f:
    doc = DoclingDocument.model_validate(json.load(f))
print(doc.export_to_markdown())
```

Does the markdown read naturally?

- [ ] **Step 5: Document any issues found and iterate**

Fix any problems discovered, re-run, verify.
