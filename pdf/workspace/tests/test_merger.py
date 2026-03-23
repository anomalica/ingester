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
        {
            "type": "table",
            "text": "",
            "page": 1,
            "rows": [["Name", "Value"], ["A", "1"]],
        }
    )
    r2 = _result(
        {
            "type": "table",
            "text": "",
            "page": 2,
            "rows": [["Name", "Value"], ["B", "2"]],
        }
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
