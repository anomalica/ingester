"""Patchright (Playwright) fetcher - headless browser for sites that block simple HTTP."""

from __future__ import annotations

import asyncio

from patchright.async_api import async_playwright

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
TIMEOUT_MS = 30_000


async def _fetch_async(url: str) -> str | None:
    """Fetch a URL using a headless Chromium browser."""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=BROWSER_ARGS,
            )
            context = await browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1920, "height": 1080},
                locale="en-GB",
            )
            page = await context.new_page()
            await page.goto(url, timeout=TIMEOUT_MS, wait_until="networkidle")
            html = await page.content()
            await browser.close()
            return html
    except Exception:
        return None


def fetch(url: str) -> str | None:
    """Fetch a URL via headless Chromium. Returns HTML string or None on failure."""
    return asyncio.run(_fetch_async(url))
