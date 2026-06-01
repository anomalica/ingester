# Acquire Re-architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the ingester into three layers: host script, acquire container (fetch + type detection), and format-specific containers (webpage, pdf) under formats/.

**Architecture:** A root-level `ingest` bash script orchestrates the pipeline. It calls the acquire container to fetch and classify a URL, then routes to the matching format handler via self-discovery of `format.yaml` files. Each layer runs in its own container-magic container. A staging directory holds fetched assets between steps.

**Tech Stack:** Python 3.12, container-magic, bash, requests, patchright, trafilatura, pyyaml

**Spec:** `docs/specs/2026-03-28-acquire-rearchitecture-design.md`

---

### Task 1: Create directory skeleton and format declarations

**Files:**
- Create: `acquire/workspace/fetch/.gitkeep`
- Create: `acquire/workspace/tests/.gitkeep`
- Create: `formats/webpage/workspace/extraction/.gitkeep`
- Create: `formats/webpage/workspace/tests/.gitkeep`
- Create: `formats/webpage/format.yaml`
- Create: `formats/pdf/format.yaml`
- Create: `formats/media/format.yaml`
- Create: `formats/ebook/format.yaml`
- Modify: `.gitignore`

- [ ] **Step 1: Create acquire directory structure**

```bash
mkdir -p acquire/workspace/fetch acquire/workspace/tests
```

- [ ] **Step 2: Create formats directory structure**

```bash
mkdir -p formats/webpage/workspace/extraction formats/webpage/workspace/tests
mkdir -p formats/media formats/ebook
```

- [ ] **Step 3: Write format.yaml for webpage**

Create `formats/webpage/format.yaml`:

```yaml
name: webpage
handles:
  - text/html
  - application/xhtml+xml
```

- [ ] **Step 4: Write format.yaml for pdf**

Create `formats/pdf/format.yaml`:

```yaml
name: pdf
handles:
  - application/pdf
```

- [ ] **Step 5: Write placeholder format.yaml for media**

Create `formats/media/format.yaml`:

```yaml
name: media
handles:
  - audio/mpeg
  - audio/wav
  - audio/ogg
  - video/mp4
  - video/webm
```

- [ ] **Step 6: Write placeholder format.yaml for ebook**

Create `formats/ebook/format.yaml`:

```yaml
name: ebook
handles:
  - application/epub+zip
  - application/x-mobipocket-ebook
```

- [ ] **Step 7: Update .gitignore**

Add `staging/` to the root `.gitignore`. The full file should be:

```
.env
CLAUDE.md
__pycache__/
test-corpus/pdf/
test-corpus/media/
test-corpus/ebook/
test-corpus/web/
output/
staging/
```

- [ ] **Step 8: Commit**

```bash
git add acquire/ formats/ .gitignore
git commit -m "chore: create directory skeleton for acquire re-architecture"
```

---

### Task 2: Content type detection module (TDD)

**Files:**
- Create: `acquire/workspace/detect.py`
- Create: `acquire/workspace/tests/conftest.py`
- Create: `acquire/workspace/tests/test_detect.py`

- [ ] **Step 1: Create conftest.py for acquire tests**

Create `acquire/workspace/tests/conftest.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

- [ ] **Step 2: Write failing tests for detect module**

Create `acquire/workspace/tests/test_detect.py`:

```python
from detect import detect_from_headers, detect_from_bytes, detect_from_extension, detect


def test_detect_from_headers_html():
    assert detect_from_headers("text/html; charset=utf-8") == "text/html"


def test_detect_from_headers_pdf():
    assert detect_from_headers("application/pdf") == "application/pdf"


def test_detect_from_headers_none():
    assert detect_from_headers(None) is None


def test_detect_from_headers_empty():
    assert detect_from_headers("") is None


def test_detect_from_bytes_pdf():
    assert detect_from_bytes(b"%PDF-1.4 ...") == "application/pdf"


def test_detect_from_bytes_html_doctype():
    assert detect_from_bytes(b"<!DOCTYPE html><html>") == "text/html"


def test_detect_from_bytes_html_tag():
    assert detect_from_bytes(b"<html><head>") == "text/html"


def test_detect_from_bytes_html_with_leading_whitespace():
    assert detect_from_bytes(b"  \n<!DOCTYPE html>") == "text/html"


def test_detect_from_bytes_unknown():
    assert detect_from_bytes(b"random binary data") is None


def test_detect_from_extension_html():
    assert detect_from_extension("page.html") == "text/html"


def test_detect_from_extension_pdf():
    assert detect_from_extension("/path/to/doc.pdf") == "application/pdf"


def test_detect_from_extension_unknown():
    assert detect_from_extension("file.xyz") is None


def test_detect_from_extension_case_insensitive():
    assert detect_from_extension("DOC.PDF") == "application/pdf"


def test_detect_priority_header_first():
    result = detect(
        data=b"%PDF-1.4",
        content_type_header="text/html",
        path="file.pdf",
    )
    assert result == "text/html"


def test_detect_falls_back_to_bytes():
    result = detect(data=b"%PDF-1.4", content_type_header=None)
    assert result == "application/pdf"


def test_detect_falls_back_to_extension():
    result = detect(data=b"unknown", path="file.pdf")
    assert result == "application/pdf"


def test_detect_returns_none_when_no_signal():
    assert detect() is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /home/mark/repos/anomalica/ingester && python3 -m pytest acquire/workspace/tests/test_detect.py -v`

Expected: FAIL (ModuleNotFoundError: No module named 'detect')

- [ ] **Step 4: Implement detect.py**

Create `acquire/workspace/detect.py`:

```python
"""Content type detection from HTTP headers, magic bytes, and file extensions."""

from __future__ import annotations

from pathlib import Path

MAGIC_SIGNATURES = [
    (b"%PDF-", "application/pdf"),
    (b"<!DOCTYPE", "text/html"),
    (b"<!doctype", "text/html"),
    (b"<html", "text/html"),
    (b"<HTML", "text/html"),
]

EXTENSION_MAP = {
    ".html": "text/html",
    ".htm": "text/html",
    ".xhtml": "application/xhtml+xml",
    ".pdf": "application/pdf",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".wav": "audio/wav",
    ".webm": "video/webm",
    ".ogg": "audio/ogg",
    ".epub": "application/epub+zip",
    ".mobi": "application/x-mobipocket-ebook",
}


def detect_from_headers(content_type: str | None) -> str | None:
    """Extract MIME type from an HTTP Content-Type header value."""
    if not content_type:
        return None
    mime = content_type.split(";")[0].strip().lower()
    return mime if mime else None


def detect_from_bytes(data: bytes) -> str | None:
    """Detect content type from magic bytes at the start of data."""
    stripped = data.lstrip()
    for signature, mime_type in MAGIC_SIGNATURES:
        if stripped[: len(signature)] == signature:
            return mime_type
    return None


def detect_from_extension(path: str | Path) -> str | None:
    """Detect content type from file extension."""
    ext = Path(path).suffix.lower()
    return EXTENSION_MAP.get(ext)


def detect(
    data: bytes | None = None,
    content_type_header: str | None = None,
    path: str | Path | None = None,
) -> str | None:
    """Detect content type using all available signals.

    Priority: Content-Type header > magic bytes > file extension.
    """
    result = detect_from_headers(content_type_header)
    if result:
        return result

    if data:
        result = detect_from_bytes(data)
        if result:
            return result

    if path:
        result = detect_from_extension(path)
        if result:
            return result

    return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /home/mark/repos/anomalica/ingester && python3 -m pytest acquire/workspace/tests/test_detect.py -v`

Expected: 18 passed

- [ ] **Step 6: Commit**

```bash
git add acquire/workspace/detect.py acquire/workspace/tests/
git commit -m "feat(acquire): add content type detection module"
```

---

### Task 3: Acquire fetch layer

The fetchers change their return type from `str | None` to `tuple[bytes, str | None] | None` - returning raw bytes and the Content-Type header instead of decoded HTML strings. This allows acquire to handle non-HTML content (PDFs, media).

**Files:**
- Create: `acquire/workspace/fetch/__init__.py`
- Create: `acquire/workspace/fetch/http.py`
- Create: `acquire/workspace/fetch/wayback.py`
- Create: `acquire/workspace/fetch/patchright_fetch.py`
- Create: `acquire/workspace/tests/test_http_fetch.py`
- Create: `acquire/workspace/tests/test_wayback_fetch.py`
- Create: `acquire/workspace/tests/test_patchright_fetch.py`

- [ ] **Step 1: Write fetch/__init__.py**

Create `acquire/workspace/fetch/__init__.py`:

```python
"""Asset acquisition fetch layer.

Each fetcher takes a URL and returns (content_bytes, content_type_header)
or None on failure. The content_type_header is the raw Content-Type value
from the HTTP response (may be None for browser-based fetchers).
"""

from fetch import http, patchright_fetch, wayback

FETCHERS = [
    ("http", http.fetch),
    ("wayback", wayback.fetch),
    ("patchright", patchright_fetch.fetch),
]
```

- [ ] **Step 2: Write fetch/http.py**

Create `acquire/workspace/fetch/http.py`:

```python
"""Simple HTTP fetcher with browser-like headers."""

from __future__ import annotations

import requests

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
TIMEOUT = 30


def fetch(url: str) -> tuple[bytes, str | None] | None:
    """Fetch a URL via HTTP GET. Returns (content_bytes, content_type) or None."""
    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
        )
        response.raise_for_status()
        return (response.content, response.headers.get("Content-Type"))
    except requests.RequestException:
        return None
```

- [ ] **Step 3: Write fetch/wayback.py**

Create `acquire/workspace/fetch/wayback.py`:

```python
"""Wayback Machine fetcher - retrieves archived snapshots of web pages."""

from __future__ import annotations

import requests

AVAILABILITY_API = "https://archive.org/wayback/available"
TIMEOUT = 30


def fetch(url: str) -> tuple[bytes, str | None] | None:
    """Fetch the closest Wayback Machine snapshot. Returns (content_bytes, content_type) or None."""
    try:
        resp = requests.get(AVAILABILITY_API, params={"url": url}, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        snapshots = data.get("archived_snapshots", {})
        closest = snapshots.get("closest")
        if not closest or closest.get("status") != "200":
            return None

        archive_url = closest["url"]
        page = requests.get(archive_url, timeout=TIMEOUT)
        page.raise_for_status()
        return (page.content, page.headers.get("Content-Type"))
    except requests.RequestException:
        return None
```

- [ ] **Step 4: Write fetch/patchright_fetch.py**

Create `acquire/workspace/fetch/patchright_fetch.py`:

```python
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
TIMEOUT_MS = 60_000


async def _fetch_async(url: str) -> tuple[bytes, str | None] | None:
    """Fetch a URL using a headless Chromium browser.

    Tries networkidle first (waits for all requests to finish). If that
    times out (sites with persistent analytics connections), falls back
    to domcontentloaded which fires once the HTML is parsed.
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                ignore_default_args=["--headless"],
                args=BROWSER_ARGS + ["--headless=new"],
            )
            context = await browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1920, "height": 1080},
                locale="en-GB",
            )
            page = await context.new_page()
            try:
                await page.goto(url, timeout=TIMEOUT_MS, wait_until="networkidle")
            except Exception:
                await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
            html = await page.content()
            await browser.close()
            return (html.encode("utf-8"), "text/html")
    except Exception:
        return None


def fetch(url: str) -> tuple[bytes, str | None] | None:
    """Fetch a URL via headless Chromium. Returns (content_bytes, content_type) or None."""
    return asyncio.run(_fetch_async(url))
```

- [ ] **Step 5: Write HTTP fetch tests**

Create `acquire/workspace/tests/test_http_fetch.py`:

```python
from unittest.mock import patch, Mock

from fetch.http import fetch


@patch("fetch.http.requests.get")
def test_fetch_returns_bytes_and_content_type(mock_get):
    mock_response = Mock()
    mock_response.content = b"<html><body>Article</body></html>"
    mock_response.headers = {"Content-Type": "text/html; charset=utf-8"}
    mock_response.raise_for_status = Mock()
    mock_get.return_value = mock_response

    result = fetch("https://example.com/article")
    assert result is not None
    content, content_type = result
    assert content == b"<html><body>Article</body></html>"
    assert content_type == "text/html; charset=utf-8"


@patch("fetch.http.requests.get")
def test_fetch_returns_pdf_bytes(mock_get):
    mock_response = Mock()
    mock_response.content = b"%PDF-1.4 binary content"
    mock_response.headers = {"Content-Type": "application/pdf"}
    mock_response.raise_for_status = Mock()
    mock_get.return_value = mock_response

    result = fetch("https://example.com/doc.pdf")
    assert result is not None
    content, content_type = result
    assert content.startswith(b"%PDF-")
    assert content_type == "application/pdf"


@patch("fetch.http.requests.get")
def test_fetch_sends_browser_user_agent(mock_get):
    mock_response = Mock()
    mock_response.content = b"<html></html>"
    mock_response.headers = {}
    mock_response.raise_for_status = Mock()
    mock_get.return_value = mock_response

    fetch("https://example.com/article")
    call_kwargs = mock_get.call_args
    headers = call_kwargs.kwargs.get("headers", {}) or call_kwargs[1].get("headers", {})
    assert "User-Agent" in headers


@patch("fetch.http.requests.get")
def test_fetch_returns_none_on_http_error(mock_get):
    import requests as req

    mock_get.side_effect = req.RequestException("Connection refused")

    result = fetch("https://example.com/article")
    assert result is None


@patch("fetch.http.requests.get")
def test_fetch_returns_none_on_non_2xx(mock_get):
    import requests as req

    mock_response = Mock()
    mock_response.raise_for_status.side_effect = req.HTTPError("403 Forbidden")
    mock_get.return_value = mock_response

    result = fetch("https://example.com/article")
    assert result is None
```

- [ ] **Step 6: Write Wayback fetch tests**

Create `acquire/workspace/tests/test_wayback_fetch.py`:

```python
from unittest.mock import patch, Mock

from fetch.wayback import fetch


def _mock_availability_response(snapshot_url, status="200"):
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {
        "archived_snapshots": {
            "closest": {
                "url": snapshot_url,
                "status": status,
            }
        }
    }
    return resp


@patch("fetch.wayback.requests.get")
def test_fetch_returns_archived_content(mock_get):
    archive_url = "https://web.archive.org/web/20171216/https://example.com"
    availability_resp = _mock_availability_response(archive_url)
    page_resp = Mock()
    page_resp.content = b"<html><body>Archived article</body></html>"
    page_resp.headers = {"Content-Type": "text/html"}
    page_resp.raise_for_status = Mock()

    mock_get.side_effect = [availability_resp, page_resp]

    result = fetch("https://example.com")
    assert result is not None
    content, content_type = result
    assert content == b"<html><body>Archived article</body></html>"
    assert content_type == "text/html"


@patch("fetch.wayback.requests.get")
def test_fetch_returns_none_when_no_snapshot(mock_get):
    resp = Mock()
    resp.raise_for_status = Mock()
    resp.json.return_value = {"archived_snapshots": {}}
    mock_get.return_value = resp

    assert fetch("https://example.com") is None


@patch("fetch.wayback.requests.get")
def test_fetch_returns_none_on_non_200_snapshot(mock_get):
    resp = _mock_availability_response("https://web.archive.org/...", status="404")
    mock_get.return_value = resp

    assert fetch("https://example.com") is None


@patch("fetch.wayback.requests.get")
def test_fetch_returns_none_on_network_error(mock_get):
    import requests as req

    mock_get.side_effect = req.RequestException("Timeout")

    assert fetch("https://example.com") is None
```

- [ ] **Step 7: Write Patchright fetch tests**

Create `acquire/workspace/tests/test_patchright_fetch.py`:

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fetch.patchright_fetch import fetch


@pytest.fixture
def mock_playwright():
    page = AsyncMock()
    page.content.return_value = "<html><body>Browser content</body></html>"
    page.goto = AsyncMock()

    context = AsyncMock()
    context.new_page.return_value = page

    browser = AsyncMock()
    browser.new_context.return_value = context
    browser.close = AsyncMock()

    chromium = AsyncMock()
    chromium.launch.return_value = browser

    pw = AsyncMock()
    pw.chromium = chromium

    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=pw)
    manager.__aexit__ = AsyncMock(return_value=False)

    return manager, browser


@patch("fetch.patchright_fetch.async_playwright")
def test_fetch_returns_bytes_and_html_type(mock_ap, mock_playwright):
    manager, browser = mock_playwright
    mock_ap.return_value = manager

    result = fetch("https://example.com")
    assert result is not None
    content, content_type = result
    assert content == b"<html><body>Browser content</body></html>"
    assert content_type == "text/html"
    browser.close.assert_called_once()


@patch("fetch.patchright_fetch.async_playwright")
def test_fetch_returns_none_on_error(mock_ap):
    mock_ap.side_effect = Exception("Browser failed")

    assert fetch("https://example.com") is None
```

- [ ] **Step 8: Run all fetch tests**

Run: `cd /home/mark/repos/anomalica/ingester && python3 -m pytest acquire/workspace/tests/ -v`

Expected: 29 passed (18 detect + 5 http + 4 wayback + 2 patchright)

- [ ] **Step 9: Commit**

```bash
git add acquire/workspace/fetch/ acquire/workspace/tests/test_http_fetch.py acquire/workspace/tests/test_wayback_fetch.py acquire/workspace/tests/test_patchright_fetch.py
git commit -m "feat(acquire): add fetch layer with bytes+content_type interface"
```

---

### Task 4: Acquire CLI and container config (TDD)

**Files:**
- Create: `acquire/workspace/acquire.py`
- Create: `acquire/workspace/tests/test_acquire.py`
- Create: `acquire/cm.yaml`

- [ ] **Step 1: Write failing tests for acquire CLI**

Create `acquire/workspace/tests/test_acquire.py`:

```python
import json
from unittest.mock import patch

from acquire import acquire


def _patch_fetchers(http_result=None, wayback_result=None, patchright_result=None):
    """Patch FETCHERS with controlled return values."""

    def _make_fetcher(result):
        def fetcher(url):
            return result

        return fetcher

    fetchers = [("http", _make_fetcher(http_result))]
    if wayback_result is not None or patchright_result is not None:
        fetchers.append(("wayback", _make_fetcher(wayback_result)))
    if patchright_result is not None:
        fetchers.append(("patchright", _make_fetcher(patchright_result)))

    return patch("acquire.FETCHERS", fetchers)


def test_acquire_writes_html_asset(tmp_path):
    html_bytes = b"<html><body>Article content here with enough data</body></html>" * 20
    with _patch_fetchers(http_result=(html_bytes, "text/html; charset=utf-8")):
        exit_code = acquire("https://example.com/article", tmp_path)

    assert exit_code == 0
    assert (tmp_path / "asset.html").exists()
    assert (tmp_path / "asset.html").read_bytes() == html_bytes


def test_acquire_writes_pdf_asset(tmp_path):
    pdf_bytes = b"%PDF-1.4 binary content"
    with _patch_fetchers(http_result=(pdf_bytes, "application/pdf")):
        exit_code = acquire("https://example.com/doc.pdf", tmp_path)

    assert exit_code == 0
    assert (tmp_path / "asset.pdf").exists()
    assert (tmp_path / "asset.pdf").read_bytes() == pdf_bytes


def test_acquire_writes_manifest(tmp_path):
    html_bytes = b"<html><body>Content</body></html>" * 50
    with _patch_fetchers(http_result=(html_bytes, "text/html")):
        acquire("https://example.com/article", tmp_path)

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["source"] == "https://example.com/article"
    assert manifest["asset"] == "asset.html"
    assert manifest["detected_type"] == "text/html"
    assert manifest["fetch_method"] == "http"
    assert "fetched_at" in manifest


def test_acquire_manifest_for_pdf(tmp_path):
    pdf_bytes = b"%PDF-1.4 binary content"
    with _patch_fetchers(http_result=(pdf_bytes, "application/pdf")):
        acquire("https://example.com/doc.pdf", tmp_path)

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["detected_type"] == "application/pdf"
    assert manifest["asset"] == "asset.pdf"


def test_acquire_falls_back_to_wayback(tmp_path):
    wayback_bytes = b"<html><body>Archived article with plenty of content</body></html>" * 20
    with _patch_fetchers(http_result=None, wayback_result=(wayback_bytes, "text/html")):
        exit_code = acquire("https://example.com/article", tmp_path)

    assert exit_code == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["fetch_method"] == "wayback"


def test_acquire_skips_small_html_response(tmp_path):
    tiny_html = b"<html>403</html>"
    big_html = b"<html><body>Real archived article content</body></html>" * 50
    with _patch_fetchers(
        http_result=(tiny_html, "text/html"),
        wayback_result=(big_html, "text/html"),
    ):
        exit_code = acquire("https://example.com/article", tmp_path)

    assert exit_code == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["fetch_method"] == "wayback"


def test_acquire_accepts_small_pdf(tmp_path):
    """Small PDFs are valid - the size check only applies to HTML."""
    small_pdf = b"%PDF-1.4 tiny"
    with _patch_fetchers(http_result=(small_pdf, "application/pdf")):
        exit_code = acquire("https://example.com/doc.pdf", tmp_path)

    assert exit_code == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["fetch_method"] == "http"


def test_acquire_returns_1_when_all_fail(tmp_path):
    with _patch_fetchers(http_result=None, wayback_result=None, patchright_result=None):
        exit_code = acquire("https://example.com/article", tmp_path)

    assert exit_code == 1
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["detected_type"] is None
    assert "error" in manifest


def test_acquire_creates_staging_dir(tmp_path):
    staging = tmp_path / "nonexistent" / "subdir"
    html_bytes = b"<html><body>Content</body></html>" * 50
    with _patch_fetchers(http_result=(html_bytes, "text/html")):
        acquire("https://example.com/article", staging)

    assert staging.exists()
    assert (staging / "manifest.json").exists()


def test_acquire_detects_type_from_bytes_when_no_header(tmp_path):
    """Content-Type header missing - fall back to magic bytes."""
    pdf_bytes = b"%PDF-1.4 binary content"
    with _patch_fetchers(http_result=(pdf_bytes, None)):
        acquire("https://example.com/mysterious-url", tmp_path)

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["detected_type"] == "application/pdf"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/mark/repos/anomalica/ingester && python3 -m pytest acquire/workspace/tests/test_acquire.py -v`

Expected: FAIL (ModuleNotFoundError: No module named 'acquire')

- [ ] **Step 3: Implement acquire.py**

Create `acquire/workspace/acquire.py`:

```python
#!/usr/bin/env python3
"""Asset acquisition - fetches a URL and writes the result to a staging directory."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from detect import detect
from fetch import FETCHERS

MIME_TO_EXT = {
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "application/pdf": ".pdf",
    "audio/mpeg": ".mp3",
    "video/mp4": ".mp4",
    "audio/wav": ".wav",
    "video/webm": ".webm",
    "audio/ogg": ".ogg",
    "application/epub+zip": ".epub",
}

MIN_HTML_SIZE = 1024


def acquire(url: str, staging_dir: Path) -> int:
    """Fetch a URL and write the asset and manifest to staging_dir.

    Returns 0 on success, 1 on failure.
    """
    staging_dir.mkdir(parents=True, exist_ok=True)

    for method_name, fetcher in FETCHERS:
        print(f"Trying {method_name}...", file=sys.stderr)
        result = fetcher(url)
        if result is None:
            print(f"  {method_name}: no response", file=sys.stderr)
            continue

        content, content_type_header = result
        detected_type = detect(
            data=content,
            content_type_header=content_type_header,
        )

        if not detected_type:
            detected_type = "application/octet-stream"

        is_html = detected_type in ("text/html", "application/xhtml+xml")

        if is_html and len(content) < MIN_HTML_SIZE:
            print(
                f"  {method_name}: response too small ({len(content)} bytes), trying next",
                file=sys.stderr,
            )
            continue

        ext = MIME_TO_EXT.get(detected_type, ".bin")
        asset_name = f"asset{ext}"
        (staging_dir / asset_name).write_bytes(content)

        manifest = {
            "source": url,
            "asset": asset_name,
            "detected_type": detected_type,
            "fetch_method": method_name,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        (staging_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

        print(f"  {method_name}: success ({detected_type})", file=sys.stderr)
        return 0

    manifest = {
        "source": url,
        "asset": None,
        "detected_type": None,
        "fetch_method": None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "error": "All fetch methods exhausted",
    }
    (staging_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("All fetch methods exhausted", file=sys.stderr)
    return 1


def main():
    parser = argparse.ArgumentParser(description="Fetch a URL and stage the result.")
    parser.add_argument("url", help="URL to fetch")
    parser.add_argument(
        "--staging-dir", required=True, type=Path, help="Staging directory path"
    )
    args = parser.parse_args()
    sys.exit(acquire(args.url, args.staging_dir))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/mark/repos/anomalica/ingester && python3 -m pytest acquire/workspace/tests/test_acquire.py -v`

Expected: 10 passed

- [ ] **Step 5: Run all acquire tests together**

Run: `cd /home/mark/repos/anomalica/ingester && python3 -m pytest acquire/workspace/tests/ -v`

Expected: 39 passed (18 detect + 5 http + 4 wayback + 2 patchright + 10 acquire)

- [ ] **Step 6: Create acquire cm.yaml**

Create `acquire/cm.yaml`:

```yaml
names:
  image: anomalica-ingester-acquire
  workspace: workspace
  user: nonroot

runtime:
  features: []
  volumes:
    - ../staging:/mnt/staging:rw

stages:
  base:
    from: python:3.12-slim
    steps:
      - env:
          PLAYWRIGHT_BROWSERS_PATH: /opt/playwright
      - apt-get:
          install:
            - libnss3
            - libnspr4
            - libatk1.0-0
            - libatk-bridge2.0-0
            - libcups2
            - libdrm2
            - libdbus-1-3
            - libxkbcommon0
            - libatspi2.0-0
            - libxcomposite1
            - libxdamage1
            - libxfixes3
            - libxrandr2
            - libgbm1
            - libasound2
            - libpango-1.0-0
            - libcairo2
      - pip:
          install:
            - requests
            - pyyaml
            - patchright
            - playwright
      - run:
          - mkdir -p /opt/playwright && chmod 777 /opt/playwright
          - playwright install chromium
          - patchright install chromium

  development:
    from: base
    steps:
      - pip:
          install:
            - pytest

  production:
    from: base
    steps:
      - copy: workspace

commands:
  acquire:
    command: python workspace/acquire.py
    description: Fetch a URL and stage the result for format-specific processing
    env:
      PYTHONUNBUFFERED: "1"
      PYTHONPATH: workspace
```

- [ ] **Step 7: Commit**

```bash
git add acquire/
git commit -m "feat(acquire): add acquisition CLI with staging output and container config"
```

---

### Task 5: Webpage format handler (TDD)

Move trafilatura extraction from web/ and create a new ingester that reads from staging instead of fetching directly.

**Files:**
- Create: `formats/webpage/workspace/extraction/__init__.py`
- Create: `formats/webpage/workspace/extraction/trafilatura_ext.py`
- Create: `formats/webpage/workspace/ingest_webpage.py`
- Create: `formats/webpage/workspace/tests/conftest.py`
- Create: `formats/webpage/workspace/tests/test_trafilatura_ext.py`
- Create: `formats/webpage/workspace/tests/test_ingest_webpage.py`
- Create: `formats/webpage/cm.yaml`

- [ ] **Step 1: Copy extraction module from web/**

Create `formats/webpage/workspace/extraction/__init__.py`:

```python
"""Web content extraction layer."""
```

Copy `web/workspace/extraction/trafilatura_ext.py` to `formats/webpage/workspace/extraction/trafilatura_ext.py` (identical content - no changes needed).

- [ ] **Step 2: Create conftest.py**

Create `formats/webpage/workspace/tests/conftest.py`:

```python
import sys
from pathlib import Path

workspace = Path(__file__).resolve().parent.parent
shared = workspace.parent.parent.parent.parent / "shared"
container_shared = Path("/mnt/shared")
if container_shared.exists():
    sys.path.insert(0, str(container_shared))
else:
    sys.path.insert(0, str(shared))
sys.path.insert(0, str(workspace))
```

- [ ] **Step 3: Copy trafilatura tests**

Copy `web/workspace/tests/test_trafilatura_ext.py` to `formats/webpage/workspace/tests/test_trafilatura_ext.py` (identical content - no changes needed).

- [ ] **Step 4: Run trafilatura tests in new location**

Run: `cd /home/mark/repos/anomalica/ingester && python3 -m pytest formats/webpage/workspace/tests/test_trafilatura_ext.py -v`

Expected: 7 passed

- [ ] **Step 5: Write failing tests for ingest_webpage.py**

Create `formats/webpage/workspace/tests/test_ingest_webpage.py`:

```python
import json
from pathlib import Path
from unittest.mock import patch

from extraction.trafilatura_ext import Article

import ingest_webpage


SAMPLE_ARTICLE = Article(
    text="# Test Article\n\nFirst paragraph of article content.\n\nSecond paragraph.",
    title="Test Article",
    authors=["Jane Smith"],
    date="2023-06-05",
    sitename="Example News",
    description="A test article",
)


def _create_staging(tmp_path, html="<html><body>Article</body></html>", url="https://example.com/article"):
    """Create a staging directory with a manifest and HTML asset."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "asset.html").write_text(html)
    manifest = {
        "source": url,
        "asset": "asset.html",
        "detected_type": "text/html",
        "fetch_method": "http",
        "fetched_at": "2026-03-28T10:00:00Z",
    }
    (staging / "manifest.json").write_text(json.dumps(manifest))
    return staging


@patch("ingest_webpage.extract_article", return_value=SAMPLE_ARTICLE)
def test_ingest_writes_record_to_store(mock_extract, tmp_path):
    staging = _create_staging(tmp_path)
    output = tmp_path / "output"

    ingest_webpage.run(staging, output, force=False)

    md_files = list((output / "store").glob("*.md"))
    assert len(md_files) == 1
    content = md_files[0].read_text()
    assert "schema: anomalica/record/1" in content
    assert "source_type: web" in content
    assert "source_url: https://example.com/article" in content
    assert "Test Article" in content


@patch("ingest_webpage.extract_article", return_value=SAMPLE_ARTICLE)
def test_ingest_writes_metadata(mock_extract, tmp_path):
    staging = _create_staging(tmp_path)
    output = tmp_path / "output"

    ingest_webpage.run(staging, output, force=False)

    meta_files = list((output / "store").glob("*.meta.json"))
    assert len(meta_files) == 1
    meta = json.loads(meta_files[0].read_text())
    assert meta["input_url"] == "https://example.com/article"
    assert meta["fetch_method"] == "http"
    assert "duration_ms" in meta
    assert "trafilatura_metadata" in meta


@patch("ingest_webpage.extract_article", return_value=SAMPLE_ARTICLE)
def test_ingest_creates_symlink(mock_extract, tmp_path):
    staging = _create_staging(tmp_path)
    output = tmp_path / "output"

    ingest_webpage.run(staging, output, force=False)

    links = list((output / "records").glob("*.md"))
    assert len(links) == 1
    assert links[0].is_symlink()
    assert "2023-06-05-web-test-article" in links[0].name


@patch("ingest_webpage.extract_article", return_value=SAMPLE_ARTICLE)
def test_ingest_skips_when_exists(mock_extract, tmp_path):
    staging = _create_staging(tmp_path)
    output = tmp_path / "output"

    ingest_webpage.run(staging, output, force=False)
    ingest_webpage.run(staging, output, force=False)

    md_files = list((output / "store").glob("*.md"))
    assert len(md_files) == 1


@patch("ingest_webpage.extract_article", return_value=SAMPLE_ARTICLE)
def test_ingest_re_extracts_with_force(mock_extract, tmp_path):
    staging = _create_staging(tmp_path)
    output = tmp_path / "output"

    ingest_webpage.run(staging, output, force=False)
    ingest_webpage.run(staging, output, force=True)

    assert mock_extract.call_count == 2


@patch("ingest_webpage.extract_article", return_value=None)
def test_ingest_exits_when_extraction_fails(mock_extract, tmp_path):
    staging = _create_staging(tmp_path)
    output = tmp_path / "output"

    exit_code = ingest_webpage.run(staging, output, force=False)
    assert exit_code != 0


def test_ingest_exits_when_no_manifest(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    output = tmp_path / "output"

    exit_code = ingest_webpage.run(staging, output, force=False)
    assert exit_code != 0


def test_ingest_exits_when_no_asset(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    manifest = {"source": "https://example.com", "asset": "asset.html"}
    (staging / "manifest.json").write_text(json.dumps(manifest))
    output = tmp_path / "output"

    exit_code = ingest_webpage.run(staging, output, force=False)
    assert exit_code != 0
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `cd /home/mark/repos/anomalica/ingester && python3 -m pytest formats/webpage/workspace/tests/test_ingest_webpage.py -v`

Expected: FAIL (ModuleNotFoundError: No module named 'ingest_webpage')

- [ ] **Step 7: Implement ingest_webpage.py**

Create `formats/webpage/workspace/ingest_webpage.py`:

```python
#!/usr/bin/env python3
"""Webpage ingester - extracts structured content from pre-fetched HTML."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from hashing import content_hash_label, hash_string, store_exists
from record import write_record
from validator import validate

from extraction.trafilatura_ext import extract_article


def _build_frontmatter(
    title: str, date: str, url: str, authors: list[str] | None, hex_hash: str
) -> str:
    """Assemble YAML frontmatter for a web record."""
    lines = [
        "---",
        "schema: anomalica/record/1",
    ]
    escaped_title = title.replace('"', '\\"')
    lines.append(f'title: "{escaped_title}"')
    lines.extend(
        [
            f"date: {date}",
            "source_type: web",
            f"source_url: {url}",
        ]
    )
    if authors:
        lines.append("authors:")
        for author in authors:
            lines.append(f"  - {author}")
    lines.append(f"content_hash: {content_hash_label(hex_hash)}")
    lines.append("---")
    return "\n".join(lines)


def run(staging_dir: Path, output_dir: Path, force: bool) -> int:
    """Run the webpage ingestion pipeline. Returns 0 on success, 1 on failure."""
    store_dir = output_dir / "store"
    records_dir = output_dir / "records"
    start_time = time.monotonic()

    manifest_path = staging_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"Error: no manifest.json in {staging_dir}", file=sys.stderr)
        return 1

    manifest = json.loads(manifest_path.read_text())
    url = manifest["source"]
    asset_name = manifest["asset"]
    fetch_method = manifest.get("fetch_method", "unknown")

    asset_path = staging_dir / asset_name
    if not asset_path.exists():
        print(f"Error: asset not found: {asset_path}", file=sys.stderr)
        return 1

    html = asset_path.read_text(encoding="utf-8", errors="replace")

    article = extract_article(html, url)
    if article is None:
        print("No article content extracted", file=sys.stderr)
        return 1

    print(f"Extracted: {article.title}", file=sys.stderr)

    hex_hash = hash_string(article.text)

    if not force and store_exists(store_dir, hex_hash):
        print(
            f"Skipping: record already exists (hash: {hex_hash[:12]}...)",
            file=sys.stderr,
        )
        return 0

    date = article.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title = article.title or "Untitled"
    frontmatter = _build_frontmatter(title, date, url, article.authors, hex_hash)
    content = frontmatter + "\n\n" + article.text + "\n"

    result = validate(content, extra_required=["source_url"])
    if result.fixed:
        content = result.fixed
    for warning in result.warnings:
        print(f"Validation warning: {warning}", file=sys.stderr)
    for error in result.errors:
        print(f"Validation error: {error}", file=sys.stderr)

    duration_ms = int((time.monotonic() - start_time) * 1000)
    metadata = {
        "input_url": url,
        "input_hash": content_hash_label(hex_hash),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "fetch_method": fetch_method,
        "duration_ms": duration_ms,
        "trafilatura_metadata": {
            "title": article.title,
            "authors": article.authors,
            "date": article.date,
            "sitename": article.sitename,
            "description": article.description,
        },
    }

    record_path, link_path = write_record(
        store_dir, records_dir, hex_hash, content, metadata, date, "web", title
    )
    print(f"Written: {record_path}", file=sys.stderr)
    print(f"Symlink: {link_path}", file=sys.stderr)
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Extract content from pre-fetched HTML into Anomalica record format."
    )
    parser.add_argument("staging_dir", type=Path, help="Path to staging directory")
    parser.add_argument(
        "--force", action="store_true", help="Re-extract even if output exists"
    )
    args = parser.parse_args()

    output_dir = Path("/mnt/output")
    if not output_dir.exists():
        output_dir = Path(__file__).resolve().parent.parent.parent.parent / "output"

    sys.exit(run(args.staging_dir, output_dir, args.force))


if __name__ == "__main__":
    main()
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd /home/mark/repos/anomalica/ingester && python3 -m pytest formats/webpage/workspace/tests/ -v`

Expected: 15 passed (7 trafilatura + 8 ingest_webpage)

- [ ] **Step 9: Create webpage cm.yaml**

Create `formats/webpage/cm.yaml`:

```yaml
names:
  image: anomalica-ingester-webpage
  workspace: workspace
  user: nonroot

runtime:
  features: []
  volumes:
    - ../../staging:/mnt/staging:ro
    - ../../output:/mnt/output:rw
    - ../../shared:/mnt/shared:ro

stages:
  base:
    from: python:3.12-slim
    steps:
      - pip:
          install:
            - trafilatura
            - pyyaml

  development:
    from: base
    steps:
      - pip:
          install:
            - pytest

  production:
    from: base
    steps:
      - copy: workspace

commands:
  ingest:
    command: python workspace/ingest_webpage.py
    description: Extract structured content from a pre-fetched web page
    env:
      PYTHONUNBUFFERED: "1"
      PYTHONPATH: "/mnt/shared"
```

- [ ] **Step 10: Commit**

```bash
git add formats/webpage/
git commit -m "feat(webpage): add webpage format handler with staging input"
```

---

### Task 6: Move PDF to formats/pdf/

Move the existing PDF ingester under formats/ and add staging directory support as an alternative input mode.

**Files:**
- Move: `pdf/` to `formats/pdf/`
- Modify: `formats/pdf/cm.yaml` (update volume paths)
- Modify: `formats/pdf/workspace/ingest_pdf.py` (add --staging-dir argument)
- Fix: `formats/pdf/workspace/shared` symlink (update target)

- [ ] **Step 1: Move pdf/ to formats/pdf/**

```bash
cd /home/mark/repos/anomalica/ingester
git mv pdf formats/pdf
```

- [ ] **Step 2: Fix the shared symlink**

The existing symlink at `pdf/workspace/shared` points to an absolute path that still works, but it should be updated to be relative for portability:

```bash
cd /home/mark/repos/anomalica/ingester
rm formats/pdf/workspace/shared
ln -s ../../../shared formats/pdf/workspace/shared
```

- [ ] **Step 3: Update cm.yaml volume paths**

Edit `formats/pdf/cm.yaml` - add staging and output volume mounts:

```yaml
names:
  image: anomalica-ingester-pdf
  workspace: workspace
  user: nonroot

runtime:
  features: []
  volumes:
    - ~/.local/bin/claude:/usr/local/bin/claude:ro
    - ~/.claude:~/.claude
    - ../../staging:/mnt/staging:ro
    - ../../output:/mnt/output:rw

stages:
  base:
    from: python:3.12-slim
    steps:
      - apt-get:
          install:
            - curl
      - pip:
          install:
            - anthropic
            - pikepdf
            - pyyaml

  development:
    from: base
    steps:
      - pip:
          install:
            - pytest

  production:
    from: base
    steps:
      - copy: workspace

commands:
  ingest:
    command: python workspace/ingest_pdf.py
    description: Extract structured content from a PDF
    env:
      PYTHONUNBUFFERED: "1"
      PYTHONPATH: workspace
    mounts:
      input:
        mode: ro
      output:
        mode: rw
```

- [ ] **Step 4: Add --staging-dir argument to ingest_pdf.py**

Edit `formats/pdf/workspace/ingest_pdf.py` - modify the `main()` function to accept a staging directory as an alternative to a direct file path. Add this argument to the argument parser and add a staging path resolution block before the existing input file detection:

At the top of `main()`, after argument parsing, add:

```python
    parser.add_argument(
        "--staging-dir",
        type=Path,
        help="Path to staging directory (alternative to input_file)",
    )
```

Then after `args = parser.parse_args()`, add before the existing mount path detection:

```python
    # If staging dir provided, read the asset path from the manifest
    if args.staging_dir:
        import json

        manifest_path = args.staging_dir / "manifest.json"
        if not manifest_path.exists():
            print(f"Error: no manifest.json in {args.staging_dir}", file=sys.stderr)
            sys.exit(1)
        manifest = json.loads(manifest_path.read_text())
        args.input_file = args.staging_dir / manifest["asset"]
```

- [ ] **Step 5: Run existing PDF tests to verify nothing is broken**

Run: `cd /home/mark/repos/anomalica/ingester && python3 -m pytest formats/pdf/workspace/tests/ -v`

Expected: All existing tests pass (the tests don't depend on the directory location since they use relative imports via PYTHONPATH)

- [ ] **Step 6: Regenerate container-magic files**

```bash
cd /home/mark/repos/anomalica/ingester/formats/pdf
cm update
```

- [ ] **Step 7: Commit**

```bash
git add formats/pdf/ -A
git commit -m "refactor(pdf): move to formats/pdf/ and add staging input support"
```

---

### Task 7: Host script with format routing

**Files:**
- Create: `ingest`

- [ ] **Step 1: Write the host script**

Create `ingest`:

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STAGING_DIR="${SCRIPT_DIR}/staging"

usage() {
    echo "Usage: ingest [--force] <url-or-path>" >&2
    echo "" >&2
    echo "Acquire a URL or local file and route to the appropriate format handler." >&2
    echo "" >&2
    echo "Options:" >&2
    echo "  --force    Re-process even if output already exists" >&2
    exit 1
}

FORCE=""
INPUT=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --force) FORCE="--force"; shift ;;
        --help|-h) usage ;;
        -*) echo "Unknown option: $1" >&2; usage ;;
        *)
            if [[ -n "$INPUT" ]]; then
                echo "Error: multiple inputs not supported" >&2
                usage
            fi
            INPUT="$1"; shift
            ;;
    esac
done

if [[ -z "$INPUT" ]]; then
    usage
fi

UUID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
RUN_DIR="${STAGING_DIR}/${UUID}"
mkdir -p "$RUN_DIR"

echo "Run: ${UUID}" >&2

is_url() {
    [[ "$1" =~ ^https?:// ]]
}

if is_url "$INPUT"; then
    echo "Acquiring: ${INPUT}" >&2
    (cd "${SCRIPT_DIR}/acquire" && cm run acquire "$INPUT" -- --staging-dir "/mnt/staging/${UUID}")
else
    if [[ ! -f "$INPUT" ]]; then
        echo "Error: file not found: ${INPUT}" >&2
        exit 1
    fi

    RESOLVED="$(realpath "$INPUT")"
    EXT="${RESOLVED##*.}"
    cp "$RESOLVED" "${RUN_DIR}/asset.${EXT}"

    DETECTED_TYPE="$(python3 -c "
import sys
sys.path.insert(0, '${SCRIPT_DIR}/acquire/workspace')
from detect import detect_from_bytes, detect_from_extension
with open('${RUN_DIR}/asset.${EXT}', 'rb') as f:
    data = f.read(8192)
result = detect_from_bytes(data)
if not result:
    result = detect_from_extension('${RUN_DIR}/asset.${EXT}')
print(result or 'application/octet-stream')
")"

    python3 -c "
import json
from datetime import datetime, timezone
manifest = {
    'source': '${RESOLVED}',
    'asset': 'asset.${EXT}',
    'detected_type': '${DETECTED_TYPE}',
    'fetch_method': 'local',
    'fetched_at': datetime.now(timezone.utc).isoformat(),
}
with open('${RUN_DIR}/manifest.json', 'w') as f:
    json.dump(manifest, f, indent=2)
"
    echo "Local file staged: ${DETECTED_TYPE}" >&2
fi

MANIFEST="${RUN_DIR}/manifest.json"
if [[ ! -f "$MANIFEST" ]]; then
    echo "Error: no manifest found after acquisition" >&2
    exit 1
fi

DETECTED_TYPE="$(python3 -c "import json; m=json.load(open('${MANIFEST}')); print(m.get('detected_type') or '')")"
ASSET="$(python3 -c "import json; m=json.load(open('${MANIFEST}')); print(m.get('asset') or '')")"

if [[ -z "$DETECTED_TYPE" || -z "$ASSET" ]]; then
    ERROR="$(python3 -c "import json; m=json.load(open('${MANIFEST}')); print(m.get('error', 'unknown error'))")"
    echo "Error: acquisition failed - ${ERROR}" >&2
    exit 1
fi

echo "Detected type: ${DETECTED_TYPE}" >&2

HANDLER=""
for format_yaml in "${SCRIPT_DIR}"/formats/*/format.yaml; do
    FORMAT_DIR="$(dirname "$format_yaml")"
    FORMAT_NAME="$(basename "$FORMAT_DIR")"

    if python3 -c "
import yaml, sys
with open('${format_yaml}') as f:
    fmt = yaml.safe_load(f)
sys.exit(0 if '${DETECTED_TYPE}' in fmt.get('handles', []) else 1)
"; then
        HANDLER="$FORMAT_NAME"
        break
    fi
done

if [[ -z "$HANDLER" ]]; then
    echo "Error: no format handler for type: ${DETECTED_TYPE}" >&2
    echo "Staging directory preserved: ${RUN_DIR}" >&2
    exit 1
fi

echo "Routing to: ${HANDLER}" >&2

case "$HANDLER" in
    webpage)
        (cd "${SCRIPT_DIR}/formats/webpage" && cm run ingest "/mnt/staging/${UUID}" -- $FORCE)
        ;;
    pdf)
        (cd "${SCRIPT_DIR}/formats/pdf" && cm run ingest -- --staging-dir "/mnt/staging/${UUID}" $FORCE)
        ;;
    *)
        echo "Error: handler '${HANDLER}' is not yet implemented" >&2
        exit 1
        ;;
esac

echo "Done. Staging: ${RUN_DIR}" >&2
```

- [ ] **Step 2: Make it executable**

```bash
chmod +x /home/mark/repos/anomalica/ingester/ingest
```

- [ ] **Step 3: Commit**

```bash
git add ingest
git commit -m "feat: add host script for acquisition routing"
```

---

### Task 8: Update justfile, clean up, and verify

**Files:**
- Modify: `justfile`
- Modify: `.gitignore`
- Remove: `web/` directory (after verifying all code is migrated)

- [ ] **Step 1: Update justfile**

Replace the contents of `justfile` with:

```justfile
ingest URL_OR_PATH *FLAGS:
    #!/usr/bin/env bash
    set -euo pipefail
    ./ingest {{FLAGS}} "{{URL_OR_PATH}}"

ingest-pdf FILE:
    #!/usr/bin/env bash
    set -euo pipefail
    mkdir -p output/store output/records
    cd formats/pdf
    cm run ingest input="$(realpath ../../{{FILE}})" output="$(realpath ../../output/)" -- --force

test-web-extract URL:
    #!/usr/bin/env bash
    set -euo pipefail
    ./ingest --force "{{URL}}"

test-web-corpus:
    #!/usr/bin/env bash
    set -euo pipefail
    python3 -c "
    import yaml
    with open('test-corpus/sources.yaml') as f:
        sources = yaml.safe_load(f)
    for entry in sources.get('web', []):
        print(entry['url'])
    " | while read -r url; do
        echo "Extracting: $url"
        ./ingest --force "$url" || echo "FAILED: $url"
    done

test-acquire:
    #!/usr/bin/env bash
    set -euo pipefail
    python3 -m pytest acquire/workspace/tests/ -v

test-webpage:
    #!/usr/bin/env bash
    set -euo pipefail
    python3 -m pytest formats/webpage/workspace/tests/ -v

test-pdf:
    #!/usr/bin/env bash
    set -euo pipefail
    cd formats/pdf
    cm run pytest workspace/tests/ -v

test-shared:
    #!/usr/bin/env bash
    set -euo pipefail
    python3 -m pytest shared/tests/ -v

test-all:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "=== shared ==="
    python3 -m pytest shared/tests/ -v
    echo ""
    echo "=== acquire ==="
    python3 -m pytest acquire/workspace/tests/ -v
    echo ""
    echo "=== webpage ==="
    python3 -m pytest formats/webpage/workspace/tests/ -v
    echo ""
    echo "=== pdf ==="
    cd formats/pdf && cm run pytest workspace/tests/ -v

download-test-corpus: download-test-corpus-pdf

download-test-corpus-pdf:
    #!/usr/bin/env bash
    set -euo pipefail
    cd test-corpus
    python3 -c "
    import yaml, subprocess, sys
    from pathlib import Path

    with open('sources.yaml') as f:
        sources = yaml.safe_load(f)

    skipped = 0
    downloaded = 0
    manual = 0

    for entry in sources.get('pdf', []):
        path = Path(entry['path'])
        if path.exists():
            print(f'Skipping: {path} (already exists)')
            skipped += 1
            continue
        if entry.get('manual'):
            print(f'Manual:   {path} - {entry.get(\"note\", \"download manually\")}')
            manual += 1
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f'Downloading: {path}')
        subprocess.run(['curl', '-fsSL', '-o', str(path), entry['url']], check=True)
        downloaded += 1

    print(f'Done. {downloaded} downloaded, {skipped} skipped, {manual} manual.')
    "
```

- [ ] **Step 2: Add container-magic generated files to gitignore**

Add to `.gitignore`:

```
# Container-magic generated files
acquire/Dockerfile
acquire/build.sh
acquire/run.sh
formats/webpage/Dockerfile
formats/webpage/build.sh
formats/webpage/run.sh
formats/pdf/Dockerfile
formats/pdf/build.sh
formats/pdf/run.sh
```

Note: Check if the existing `web/.gitignore` has these patterns and whether they should be kept or if the root `.gitignore` covers them.

- [ ] **Step 3: Run all non-containerised tests**

Run: `cd /home/mark/repos/anomalica/ingester && python3 -m pytest shared/tests/ acquire/workspace/tests/ formats/webpage/workspace/tests/ -v`

Expected: All tests pass (shared: ~17, acquire: ~39, webpage: ~15 = ~71 total)

- [ ] **Step 4: Remove old web/ directory**

Only after confirming all code has been migrated and all tests pass:

```bash
cd /home/mark/repos/anomalica/ingester
git rm -r web/
```

- [ ] **Step 5: Commit cleanup**

```bash
git add justfile .gitignore
git commit -m "chore: update justfile for new architecture, remove old web/ directory"
```

- [ ] **Step 6: Run full test suite one final time**

Run: `cd /home/mark/repos/anomalica/ingester && python3 -m pytest shared/tests/ acquire/workspace/tests/ formats/webpage/workspace/tests/ -v`

Expected: All tests pass

- [ ] **Step 7: Update CLAUDE.md**

Update the project CLAUDE.md to reflect the new architecture. Key changes:
- Directory structure section updated
- Web ingester section replaced with acquire + webpage description
- Running instructions updated for new paths
- Remove references to `web/` directory

- [ ] **Step 8: Commit CLAUDE.md update**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for acquire re-architecture"
```
