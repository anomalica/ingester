"""Render an already-loaded patchright page to a PDF for archival review.

Sizing rationale: the workbench renders these PDFs in a side-by-side review
pane (typically half of a 1080-1440p display). We capture at 1024 px wide
because that triggers full desktop layout on essentially every responsive
site. The PDF is produced as a single tall page sized to the document's
full scrollHeight, capped at a safe ceiling well below Chromium's 200-inch
(~19200 px) limit. Single-page output renders seamlessly in PDF.js with no
visible page breaks; printers that need pagination can re-paginate at
print time.
"""

from __future__ import annotations

CAPTURE_WIDTH_PX = 1024
MIN_PAGE_HEIGHT_PX = 1024
MAX_PAGE_HEIGHT_PX = 18000


async def capture_pdf(page) -> bytes:
    """Render the given patchright page to PDF bytes as a single tall page.

    Measures the document scrollHeight, clamps it to a safe range, and
    renders one page of that exact size. Uses screen media (not print)
    so the captured layout matches what a visitor saw rather than the
    publisher's print stylesheet.
    """
    await page.emulate_media(media="screen")
    scroll_height = await page.evaluate(
        "Math.max("
        "document.documentElement.scrollHeight,"
        "document.body ? document.body.scrollHeight : 0"
        ")"
    )
    height_px = max(MIN_PAGE_HEIGHT_PX, min(int(scroll_height), MAX_PAGE_HEIGHT_PX))
    return await page.pdf(
        width=f"{CAPTURE_WIDTH_PX}px",
        height=f"{height_px}px",
        print_background=True,
        margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
        prefer_css_page_size=False,
    )
