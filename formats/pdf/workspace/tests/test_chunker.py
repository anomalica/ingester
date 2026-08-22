from pathlib import Path
import io

import pikepdf
import pymupdf

from extraction.chunker import get_page_count, split_pdf

FIXTURES = Path(__file__).parent / "fixtures"


def test_get_page_count_simple():
    assert get_page_count(FIXTURES / "simple.pdf") == 1


def test_get_page_count_image_is_one(tmp_path):
    """An image is a single-page document; get_page_count must not open it with
    pikepdf (which would raise), because every page-related assumption in the
    handler is written against multi-page PDFs."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 60))
    pix.clear_with(255)
    img = tmp_path / "scan.jpg"
    pix.save(str(img))
    assert get_page_count(img) == 1


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
    for chunk in chunks:
        pdf = pikepdf.Pdf.open(io.BytesIO(chunk["pdf_data"]))
        assert len(pdf.pages) == chunk["page_count"]
