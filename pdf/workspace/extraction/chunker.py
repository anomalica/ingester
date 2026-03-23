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
