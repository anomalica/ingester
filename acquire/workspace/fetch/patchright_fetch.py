"""Patchright (Playwright) fetcher - headless browser for sites that block simple HTTP."""

from __future__ import annotations

import asyncio

from patchright.async_api import async_playwright

from captures import apply_cosmetic_filters, capture_pdf
from captures.singlefile import capture_singlefile

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
]
TIMEOUT_MS = 60_000


async def _fetch_async(url: str) -> tuple[bytes, str | None, dict] | None:
    """Fetch a URL using a headless Chromium browser and capture snapshots.

    Returns (html_bytes, content_type, metadata) where metadata includes
    a `snapshots` list of additional artefacts (currently a PDF render of
    the page). All snapshots are captured against the same DOM state, after
    cosmetic adblock and modal-strip CSS has been injected.
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                ignore_default_args=["--headless"],
                args=BROWSER_ARGS + ["--headless=new"],
            )
            context = await browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1024, "height": 1366},
                locale="en-GB",
            )
            page = await context.new_page()
            try:
                await page.goto(url, timeout=TIMEOUT_MS, wait_until="networkidle")
            except Exception:
                await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")

            await apply_cosmetic_filters(page)

            html = await page.content()
            pdf_bytes = await capture_pdf(page)

            await browser.close()

            snapshots = [
                {
                    "bytes": pdf_bytes,
                    "extension": "pdf",
                    "content_type": "application/pdf",
                    "role": "page_render",
                }
            ]

            # SingleFile spawns its own browser and re-fetches the URL with
            # all external assets inlined as data URIs. Adds latency
            # (~10-30s per page) but produces an artefact that renders
            # under sandbox="" without leaking network requests.
            singlefile_bytes = capture_singlefile(url)
            if singlefile_bytes:
                snapshots.append(
                    {
                        "bytes": singlefile_bytes,
                        "extension": "html",
                        "content_type": "text/html",
                        "role": "single_file",
                    }
                )

            metadata = {"snapshots": snapshots}
            return (html.encode("utf-8"), "text/html", metadata)
    except Exception:
        return None


def fetch(url: str) -> tuple[bytes, str | None, dict] | None:
    """Fetch a URL via headless Chromium. Returns (html, content_type, metadata) or None."""
    return asyncio.run(_fetch_async(url))
