from unittest.mock import patch, Mock

from fetch.http import fetch


@patch("fetch.http.requests.get")
def test_fetch_returns_html_on_success(mock_get):
    mock_response = Mock()
    mock_response.text = "<html><body>Article</body></html>"
    mock_response.raise_for_status = Mock()
    mock_get.return_value = mock_response

    result = fetch("https://example.com/article")
    assert result == "<html><body>Article</body></html>"
    mock_get.assert_called_once()


@patch("fetch.http.requests.get")
def test_fetch_sends_browser_user_agent(mock_get):
    mock_response = Mock()
    mock_response.text = "<html></html>"
    mock_response.raise_for_status = Mock()
    mock_get.return_value = mock_response

    fetch("https://example.com/article")
    call_kwargs = mock_get.call_args
    assert "User-Agent" in call_kwargs.kwargs.get(
        "headers", {}
    ) or "User-Agent" in call_kwargs[1].get("headers", {})


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
