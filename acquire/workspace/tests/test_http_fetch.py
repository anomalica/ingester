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
