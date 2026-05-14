"""Inject cosmetic adblock + modal-strip CSS into a live patchright page."""

from __future__ import annotations

from pathlib import Path

_CSS_PATH = Path(__file__).resolve().parent / "adblock.css"


async def apply_cosmetic_filters(page) -> None:
    """Inject the cosmetic-filter stylesheet into the given page.

    Should be called after page.goto() but before any snapshot capture so
    that hidden elements are absent from both the post-render HTML and the
    generated PDF.
    """
    css = _CSS_PATH.read_text(encoding="utf-8")
    await page.add_style_tag(content=css)
