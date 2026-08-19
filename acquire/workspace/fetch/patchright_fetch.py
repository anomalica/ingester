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

# A headless load sits at scroll offset 0, so scroll-reveal animations only
# ever fire for the above-the-fold blocks; everything below stays hidden
# (opacity:0) and renders blank in the PDF. Two steps fix this: force the
# known scroll-reveal libraries into their revealed end-state, then walk the
# viewport down the page to load anything genuinely deferred (lazy images,
# IntersectionObserver content). Scripted scrolling alone does NOT reliably
# fire Squarespace's reveal (the site's observer coalesces fast jumps), so the
# class-force is what actually recovers the article body.
SCROLL_STEP_FRACTION = 0.6
SCROLL_SETTLE_MS = 120
SCROLL_FINAL_SETTLE_MS = 700
SCROLL_MAX_STEPS = 300

# JS run in-page to reveal scroll-triggered content. Targets the three
# reveal libraries seen in the corpus: Squarespace (.preFade -> add .fadeIn),
# AOS ([data-aos] -> add .aos-animate), WOW.js (.wow). Each element also gets
# the revealed end-state forced inline so it holds even when the library's own
# rule is gated (Squarespace exempts data-override-initial-global-animation
# blocks from its .fadeIn opacity rule). display:none stays hidden - opacity:1
# does not override it - so intentionally-hidden responsive duplicates and
# adblock-stripped modals are untouched.
_REVEAL_JS = """() => {
    const show = (el) => {
        el.style.setProperty('opacity', '1', 'important');
        el.style.setProperty('transform', 'none', 'important');
    };
    document.querySelectorAll('.preFade').forEach((el) => {
        el.classList.add('fadeIn');
        show(el);
    });
    document.querySelectorAll('[data-aos]').forEach((el) => {
        el.classList.add('aos-animate');
        show(el);
    });
    document.querySelectorAll('.wow').forEach((el) => {
        el.style.setProperty('visibility', 'visible', 'important');
        el.style.setProperty('opacity', '1', 'important');
        el.style.setProperty('animation-name', 'none', 'important');
    });
}"""


async def _reveal_lazy_content(page) -> None:
    """Reveal scroll-triggered content before any snapshot.

    Squarespace (and similar) start article blocks at ``opacity:0`` via a
    ``.preFade`` class and add ``.fadeIn`` (``opacity:1``) only when the block
    scrolls into view. A headless capture never scrolls, so only the first
    screenful is ever revealed and the rest of the PDF is blank. We force the
    known reveal libraries into their end-state, then walk the viewport down
    the page so genuinely deferred resources (lazy images) still load.
    """
    await page.evaluate(_REVEAL_JS)
    await page.evaluate(
        """async ({stepFraction, settleMs, finalSettleMs, maxSteps}) => {
            const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
            const docHeight = () => Math.max(
                document.documentElement.scrollHeight,
                document.body ? document.body.scrollHeight : 0,
            );
            const step = Math.max(1, Math.floor(window.innerHeight * stepFraction));
            let y = 0;
            for (let i = 0; i < maxSteps; i++) {
                window.scrollTo(0, y);
                await sleep(settleMs);
                if (y + window.innerHeight >= docHeight()) break;
                y += step;
            }
            window.scrollTo(0, 0);
            await sleep(finalSettleMs);
        }""",
        {
            "stepFraction": SCROLL_STEP_FRACTION,
            "settleMs": SCROLL_SETTLE_MS,
            "finalSettleMs": SCROLL_FINAL_SETTLE_MS,
            "maxSteps": SCROLL_MAX_STEPS,
        },
    )
    # Re-run the reveal after scrolling: lazy content inserted during the
    # walk (late-loaded blocks) can arrive carrying .preFade of its own.
    await page.evaluate(_REVEAL_JS)


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

            # Fire scroll-triggered reveal animations and lazy content before we
            # snapshot, otherwise below-the-fold blocks render blank.
            await _reveal_lazy_content(page)
            try:
                await page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:
                pass

            # The URL after any 3xx/JS redirects - lets acquire detect a dead
            # content URL that collapsed to the site homepage.
            final_url = page.url

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

            metadata = {"snapshots": snapshots, "final_url": final_url}
            return (html.encode("utf-8"), "text/html", metadata)
    except Exception:
        return None


def fetch(url: str) -> tuple[bytes, str | None, dict] | None:
    """Fetch a URL via headless Chromium. Returns (html, content_type, metadata) or None."""
    return asyncio.run(_fetch_async(url))
