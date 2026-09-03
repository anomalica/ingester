"""Capture a self-contained HTML snapshot via single-file-cli.

The single-file-cli tool spawns its own headless Chromium, navigates to the
URL, and serialises the rendered DOM with all external resources (CSS,
fonts, images) inlined as data URIs. The result is a single HTML file that
renders identically to the original under a sandboxed iframe with no
network access - which is what the workbench review pane needs.

A capture is only worth having if it renders, so the browser runs with web
security off; see BROWSER_ARGS for what that fixes and why it is safe here.

Note: single-file runs its own browser, independent of patchright. Our
cosmetic adblock CSS is NOT applied to this snapshot. Ads visible on the
live page will appear in the SingleFile output. Acceptable for v1 - we
can pass --user-style-path in a later iteration.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TIMEOUT_SECONDS = 120
# Replaying an archived page is far slower than loading the live one: every
# subresource is served from the archive, and the Wayback Machine rate-limits.
ARCHIVE_TIMEOUT_SECONDS = 600
SINGLE_FILE_FALLBACK = "/usr/local/bin/single-file"
BROWSER_ARGS = (
    '--browser-args=["--no-sandbox","--disable-dev-shm-usage","--disable-web-security"]'
)


def _find_chromium() -> str | None:
    """Locate the patchright-installed Chromium binary inside the container."""
    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/playwright"))
    for pattern in (
        "chromium-*/chrome-linux64/chrome",
        "chromium-*/chrome-linux/chrome",
    ):
        matches = sorted(root.glob(pattern))
        if matches:
            return str(matches[-1])
    return None


def capture_singlefile(
    url: str, drop_selectors: str | None = None, timeout: int = TIMEOUT_SECONDS
) -> bytes | None:
    """Run single-file-cli against the URL and return inlined HTML bytes.

    ``timeout`` bounds the capture; an archived page needs far longer than a
    live one (ARCHIVE_TIMEOUT_SECONDS).

    ``drop_selectors`` is a comma-separated CSS selector list removed before
    serialising - used to drop an archive's replay toolbar when capturing from
    the Wayback Machine, so the snapshot shows the publisher's page and not the
    archive's chrome around it.

    Returns None if the tool fails or times out. Reuses the patchright-
    installed Chromium to avoid pulling down another browser.
    """
    chromium = _find_chromium()
    if not chromium:
        print(
            "single-file: no Chromium found under PLAYWRIGHT_BROWSERS_PATH",
            file=sys.stderr,
        )
        return None

    single_file = shutil.which("single-file") or SINGLE_FILE_FALLBACK
    if not Path(single_file).exists():
        print(f"single-file: binary not found at {single_file}", file=sys.stderr)
        return None

    # single-file refuses to write to an existing file, so we generate a
    # unique path via mkstemp and immediately remove the placeholder file.
    fd, tmp_name = tempfile.mkstemp(suffix=".html")
    os.close(fd)
    output_path = Path(tmp_name)
    output_path.unlink(missing_ok=True)

    # single-file's simple-cdp dependency references the `CloseEvent` global,
    # which the container's Node predates; without this shim single-file
    # crashes ("CloseEvent is not defined") and produces no snapshot. Injected
    # via NODE_OPTIONS so it loads before single-file's own code runs.
    polyfill = Path(__file__).resolve().parent / "closeevent_polyfill.js"
    env = dict(os.environ)
    env["NODE_OPTIONS"] = f"{env.get('NODE_OPTIONS', '')} --require {polyfill}".strip()

    try:
        result = subprocess.run(
            [
                single_file,
                url,
                str(output_path),
                f"--browser-executable-path={chromium}",
                # --disable-web-security is what makes a site's own stylesheet
                # survive the capture. SingleFile cannot read a cross-origin
                # stylesheet out of the CSSOM (the browser refuses
                # sheet.cssRules for it), so it re-fetches the URL - and that
                # fetch is subject to CORS, which a redirect breaks. Squarespace
                # serves a versioned site.css and 301s an outdated version
                # number to the current one, so every Squarespace page lost its
                # entire layout stylesheet and the snapshot rendered as
                # unstyled text (23 of our 35 web records). With web security
                # off the CSSOM is readable and the stylesheet is captured.
                # This is a throwaway browser in a container, given one URL we
                # are already fetching, with no profile and no user data.
                BROWSER_ARGS,
                # Force lazy-loaded images to materialise before capture.
                # Without these the captured DOM keeps src=data:URI
                # placeholders and the real image URLs only in data-src /
                # data-srcset, which is invisible under sandbox="" because
                # no script runs to swap them. The dispatch-scroll-event
                # flag triggers libraries that listen for scroll-into-view.
                "--load-deferred-images=true",
                "--load-deferred-images-dispatch-scroll-event=true",
                "--load-deferred-images-max-idle-time=10000",
                *(
                    [f"--removed-elements-selector={drop_selectors}"]
                    if drop_selectors
                    else []
                ),
            ],
            timeout=timeout,
            capture_output=True,
            env=env,
        )
        if result.returncode != 0:
            print(
                f"single-file: exit {result.returncode}: {result.stderr.decode('utf-8', 'replace')[:500]}",
                file=sys.stderr,
            )
            return None
        if not output_path.exists() or output_path.stat().st_size == 0:
            return None
        return output_path.read_bytes()
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(f"single-file: {exc}", file=sys.stderr)
        return None
    finally:
        output_path.unlink(missing_ok=True)
