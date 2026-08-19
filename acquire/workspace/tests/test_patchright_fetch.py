from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fetch.patchright_fetch import fetch


@pytest.fixture
def mock_playwright():
    page = AsyncMock()
    page.content.return_value = "<html><body>Browser content</body></html>"
    page.goto = AsyncMock()
    page.add_style_tag = AsyncMock()
    page.emulate_media = AsyncMock()
    page.pdf = AsyncMock(return_value=b"%PDF-1.4 fake pdf bytes")
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
    return manager, browser, page


@patch("fetch.patchright_fetch.capture_singlefile", return_value=None)
@patch("fetch.patchright_fetch.async_playwright")
def test_fetch_returns_html_and_pdf_snapshot(
    mock_ap, _mock_singlefile, mock_playwright
):
    manager, browser, page = mock_playwright
    mock_ap.return_value = manager
    result = fetch("https://example.com")
    assert result is not None
    content, content_type, metadata = result
    assert content == b"<html><body>Browser content</body></html>"
    assert content_type == "text/html"
    assert metadata["snapshots"][0]["content_type"] == "application/pdf"
    assert metadata["snapshots"][0]["role"] == "page_render"
    assert metadata["snapshots"][0]["extension"] == "pdf"
    assert metadata["snapshots"][0]["bytes"].startswith(b"%PDF")
    page.add_style_tag.assert_called_once()
    page.emulate_media.assert_called_once()
    page.pdf.assert_called_once()
    browser.close.assert_called_once()


@patch(
    "fetch.patchright_fetch.capture_singlefile",
    return_value=b"<html>inlined</html>",
)
@patch("fetch.patchright_fetch.async_playwright")
def test_fetch_includes_singlefile_when_available(mock_ap, _mock_sf, mock_playwright):
    manager, _browser, _page = mock_playwright
    mock_ap.return_value = manager
    result = fetch("https://example.com")
    assert result is not None
    _, _, metadata = result
    roles = [s["role"] for s in metadata["snapshots"]]
    assert "single_file" in roles


@patch("fetch.patchright_fetch.capture_singlefile", return_value=None)
@patch("fetch.patchright_fetch.async_playwright")
def test_fetch_reveals_scroll_hidden_content_before_capture(
    mock_ap, _mock_sf, mock_playwright
):
    manager, _browser, page = mock_playwright
    mock_ap.return_value = manager
    fetch("https://example.com")
    # The reveal step forces scroll-hidden blocks (Squarespace .preFade etc.)
    # visible via page.evaluate before the PDF is captured, otherwise the
    # snapshot would truncate to the above-the-fold content.
    reveal_js = [
        c.args[0]
        for c in page.evaluate.await_args_list
        if c.args and isinstance(c.args[0], str)
    ]
    assert any(".preFade" in js and "fadeIn" in js for js in reveal_js)


@patch("fetch.patchright_fetch.async_playwright")
def test_fetch_returns_none_on_error(mock_ap):
    mock_ap.side_effect = Exception("Browser failed")
    assert fetch("https://example.com") is None
