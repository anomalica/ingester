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
