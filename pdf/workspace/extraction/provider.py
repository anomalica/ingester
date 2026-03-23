from __future__ import annotations

from abc import ABC, abstractmethod

from extraction.models import ExtractionResult


class ExtractionProvider(ABC):
    @abstractmethod
    def extract(self, pdf_data: bytes) -> ExtractionResult:
        """Send entire PDF to model, return structured extraction."""
        ...

    @abstractmethod
    def extract_chunk(
        self, pdf_data: bytes, page_offset: int, page_count: int
    ) -> ExtractionResult:
        """Send a chunk of pages to model.
        page_offset: 1-based page number of first page in this chunk.
        page_count: number of pages in this chunk."""
        ...
