from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fetch.patchright_fetch import fetch


@pytest.fixture
def mock_playwright():
    """Set up a mock Patchright browser stack."""
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
def test_fetch_returns_html(mock_ap, mock_playwright):
    manager, browser = mock_playwright
    mock_ap.return_value = manager

    result = fetch("https://example.com")
    assert result == "<html><body>Browser content</body></html>"
    browser.close.assert_called_once()


@patch("fetch.patchright_fetch.async_playwright")
def test_fetch_returns_none_on_error(mock_ap):
    mock_ap.side_effect = Exception("Browser failed")

    result = fetch("https://example.com")
    assert result is None
