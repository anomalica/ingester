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
    content, content_type, metadata = result
    assert content == b"<html><body>Archived article</body></html>"
    assert content_type == "text/html"
    assert metadata["fetched_url"] == archive_url


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
