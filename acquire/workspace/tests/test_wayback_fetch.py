from unittest.mock import patch, Mock

from fetch.wayback import fetch


def _mock_snapshot_response(final_url, content=b"<html>archived</html>", status=200):
    resp = Mock()
    resp.status_code = status
    resp.url = final_url
    resp.content = content
    resp.headers = {"Content-Type": "text/html"}
    return resp


@patch("fetch.wayback.requests.get")
def test_fetch_returns_archived_content(mock_get):
    snapshot_url = "https://web.archive.org/web/20250102020933/https://example.com/"
    mock_get.return_value = _mock_snapshot_response(snapshot_url)
    result = fetch("https://example.com")
    assert result is not None
    content, content_type, metadata = result
    assert content == b"<html>archived</html>"
    assert content_type == "text/html"
    assert metadata["fetched_url"] == snapshot_url


@patch("fetch.wayback.requests.get")
def test_fetch_returns_none_when_no_snapshot(mock_get):
    # Wayback returns 404 when no snapshot exists for the URL
    mock_get.return_value = _mock_snapshot_response(
        "https://web.archive.org/web/2026/https://example.com", status=404
    )
    assert fetch("https://example.com") is None


@patch("fetch.wayback.requests.get")
def test_fetch_returns_none_on_non_snapshot_redirect(mock_get):
    # 200 OK but final URL does not contain a snapshot timestamp - means
    # wayback landed us on a non-snapshot page (search, info, etc.)
    mock_get.return_value = _mock_snapshot_response(
        "https://web.archive.org/web/about/", status=200
    )
    assert fetch("https://example.com") is None


@patch("fetch.wayback.requests.get")
def test_fetch_returns_none_on_network_error(mock_get):
    import requests as req

    mock_get.side_effect = req.RequestException("Timeout")
    assert fetch("https://example.com") is None
