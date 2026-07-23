from unittest.mock import patch, Mock

from fetch.wayback import _diverged, fetch, fetch_snapshot


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


def test_diverged_detects_archived_redirect():
    # a snapshot that resolved to a different path than requested (dead page
    # captured mid-redirect to /news) is flagged
    assert _diverged(
        "http://www.space.com/peopleinterviews/clarke_believe_010227.html",
        "https://www.space.com/news",
    )
    # same path, a descendant, or no landing url -> not flagged
    assert not _diverged("https://x.com/a/b", "https://x.com/a/b")
    assert not _diverged("https://x.com/a", "https://x.com/a/amp")
    assert not _diverged("https://x.com/a", None)


@patch("fetch.wayback.requests.get")
def test_fetch_skips_snapshot_that_followed_archived_redirect(mock_get):
    # every year bucket resolves to a capture of the /news redirect target, not
    # the requested article -> no valid snapshot, returns None (clean fail)
    mock_get.return_value = _mock_snapshot_response(
        "https://web.archive.org/web/20240705120000id_/https://www.space.com/news"
    )
    assert (
        fetch("http://www.space.com/peopleinterviews/clarke_believe_010227.html")
        is None
    )


@patch("fetch.wayback.requests.get")
def test_fetch_snapshot_fetches_explicit_capture_raw(mock_get):
    raw_url = (
        "https://web.archive.org/web/20010405120000id_/http://space.com/clarke.html"
    )
    mock_get.return_value = _mock_snapshot_response(
        raw_url, content=b"<html>Clarke's Believe It or Not</html>"
    )
    result = fetch_snapshot(
        "https://web.archive.org/web/20010405120000/http://space.com/clarke.html"
    )
    assert result is not None
    content, _ctype, _meta = result
    assert content == b"<html>Clarke's Believe It or Not</html>"
    assert "id_/" in mock_get.call_args_list[0][0][0]
