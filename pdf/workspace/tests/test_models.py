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
