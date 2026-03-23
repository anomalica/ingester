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
    heading_texts = [
        t["text"] for t in exported["texts"] if "header" in t.get("label", "").lower()
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
