import json
from unittest.mock import patch

from acquire import _redirected_away, _ytdlp_creators, acquire


def test_ytdlp_creators_distinct_from_channel():
    # a creator distinct from the channel is captured
    assert _ytdlp_creators({"channel": "Area52", "creator": "Chris Ramsey"}) == [
        "Chris Ramsey"
    ]
    # creator equal to the channel is dropped (it's the publisher, not a host)
    assert _ytdlp_creators({"channel": "Area52", "creator": "Area52"}) == []
    # list form, channel filtered out
    assert _ytdlp_creators(
        {"uploader": "Area52", "creators": ["Chris Ramsey", "Area52"]}
    ) == ["Chris Ramsey"]
    # nothing exposed -> empty (reviewer fills it)
    assert _ytdlp_creators({"channel": "Area52"}) == []
    # comma-separated string is split
    assert _ytdlp_creators({"artist": "A, B"}) == ["A", "B"]


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
    wayback_bytes = (
        b"<html><body>Archived article with plenty of content</body></html>" * 20
    )
    with (
        _patch_fetchers(http_result=None, wayback_result=(wayback_bytes, "text/html")),
        patch("fetch.patchright_fetch.fetch", lambda url: None),
    ):
        exit_code = acquire("https://example.com/article", tmp_path)
    assert exit_code == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["fetch_method"] == "wayback"


def test_acquire_skips_small_html_response(tmp_path):
    tiny_html = b"<html>403</html>"
    big_html = b"<html><body>Real archived article content</body></html>" * 50
    with (
        _patch_fetchers(
            http_result=(tiny_html, "text/html"),
            wayback_result=(big_html, "text/html"),
        ),
        patch("fetch.patchright_fetch.fetch", lambda url: None),
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


def test_acquire_video_url_does_not_fall_back_to_page_shell(tmp_path):
    """A youtube URL where yt-dlp fails must NOT fall through to wayback/http
    scraping the page shell - acquire fails cleanly instead."""
    html_shell = b"<html><title>- YouTube</title>" + b"x" * 500 + b"</html>"
    fetchers = [
        ("ytdlp", lambda url: None),
        ("wayback", lambda url: (html_shell, "text/html")),
    ]
    with patch("acquire.FETCHERS", fetchers):
        exit_code = acquire("https://www.youtube.com/watch?v=YBLabIhW00c", tmp_path)
    assert exit_code == 1
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["asset"] is None
    assert "video-platform" in manifest["error"]
    assert not list(tmp_path.glob("asset.*"))


def test_acquire_non_video_url_still_falls_back(tmp_path):
    """The guard must not affect ordinary URLs: a generic URL whose primary
    fetcher fails still falls back to the next fetcher."""
    html = b"<html>" + b"x" * 2000 + b"</html>"
    fetchers = [
        ("ytdlp", lambda url: None),
        ("wayback", lambda url: (html, "text/html")),
    ]
    with patch("acquire.FETCHERS", fetchers):
        exit_code = acquire("https://example.com/article", tmp_path)
    assert exit_code == 0


def test_acquire_prefers_live_original_over_archived_html(tmp_path):
    """When a page arrives via wayback, acquire fetches the live original and
    uses that capture (body + snapshots) as the source of record - an archived
    copy of a live site is often paywalled/thin."""
    archived = b"<html><body>Archived paywalled teaser</body></html>" * 30
    live = b"<html><body>The full live article, much longer</body></html>" * 200
    live_meta = {
        "snapshots": [
            {
                "extension": "html",
                "bytes": b"<html>frozen full page</html>" * 100,
                "content_type": "text/html",
                "role": "single_file",
            }
        ]
    }
    fetchers = [
        ("ytdlp", lambda url: None),
        ("wayback", lambda url: (archived, "text/html")),
    ]
    with (
        patch("acquire.FETCHERS", fetchers),
        patch(
            "fetch.patchright_fetch.fetch", lambda url: (live, "text/html", live_meta)
        ),
    ):
        exit_code = acquire("https://example.com/article", tmp_path)
    assert exit_code == 0
    assert (tmp_path / "asset.html").read_bytes() == live
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["fetch_method"] == "wayback+live"
    assert manifest["snapshots"][0]["role"] == "single_file"


def test_acquire_keeps_archived_when_live_fetch_blocked(tmp_path):
    """If the live fetch is blocked (returns nothing), the archived copy is kept
    as the fallback - the behaviour that lets headless-blocked sites still
    ingest via wayback."""
    archived = b"<html><body>Archived article content</body></html>" * 40
    fetchers = [
        ("ytdlp", lambda url: None),
        ("wayback", lambda url: (archived, "text/html")),
    ]
    with (
        patch("acquire.FETCHERS", fetchers),
        patch("fetch.patchright_fetch.fetch", lambda url: None),
    ):
        exit_code = acquire("https://example.com/article", tmp_path)
    assert exit_code == 0
    assert (tmp_path / "asset.html").read_bytes() == archived
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["fetch_method"] == "wayback"


def test_redirected_away_detection():
    # a dead article that collapses to the site root -> flagged
    assert _redirected_away(
        "https://space.com/interviews/clarke_010227.html", "https://www.space.com/"
    )
    assert _redirected_away("https://x.com/a/b/c", "https://x.com")
    # a dead article that lands on a generic section (the real space.com case:
    # ...clarke_believe.html -> /news) -> flagged
    assert _redirected_away(
        "http://www.space.com/peopleinterviews/clarke_believe_010227.html",
        "https://www.space.com/news",
    )
    # an article redirected up to its parent section -> flagged
    assert _redirected_away("https://x.com/section/article", "https://x.com/section")
    # any divergence onto an unrelated path -> flagged (keeps the archived copy
    # of what the operator actually pointed at)
    assert _redirected_away("https://x.com/old", "https://x.com/new")
    # redirects that keep the requested path (scheme/host/slash canonicalisation)
    # or descend into it -> NOT flagged
    assert not _redirected_away("http://x.com/article", "https://x.com/article")
    assert not _redirected_away("http://x.com/a", "https://x.com/a/")
    assert not _redirected_away("https://x.com/article", "https://x.com/article/amp")
    # requested was already the homepage -> nothing lost, not flagged
    assert not _redirected_away("https://x.com/", "https://x.com/")
    # no final url (live fetch failed/blocked) -> not flagged
    assert not _redirected_away("https://x.com/a", None)


def test_acquire_keeps_archived_when_live_redirects_away(tmp_path):
    # Regression for the space.com/Clarke bug: a dead article whose live original
    # 301s to a generic section (/news) must NOT override the archived copy the
    # operator pointed at.
    archived = (
        b"<html><body>Clarke's Believe It or Not - the real interview</body></html>"
        * 30
    )
    section = (
        b"<html><body>space.com News - latest stories, photo of the day</body></html>"
        * 30
    )
    wb = (
        archived,
        "text/html",
        {"fetched_url": "https://web.archive.org/web/2005id_/https://space.com/clarke"},
    )
    live = (
        section,
        "text/html",
        {"snapshots": [], "final_url": "https://www.space.com/news"},
    )
    with (
        _patch_fetchers(wayback_result=wb),
        patch("fetch.patchright_fetch.fetch", return_value=live),
    ):
        exit_code = acquire(
            "http://www.space.com/peopleinterviews/clarke_believe_010227.html", tmp_path
        )
    assert exit_code == 0
    assert (tmp_path / "asset.html").read_bytes() == archived
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["fetch_method"] == "wayback"


def test_acquire_prefers_live_when_no_redirect(tmp_path):
    # The live-first behaviour is preserved when the live original is NOT
    # redirected away - the fuller live capture still wins over a thin archive.
    archived = b"<html><body>archived thin copy</body></html>" * 30
    live_full = b"<html><body>full live article, much richer content</body></html>" * 40
    wb = (
        archived,
        "text/html",
        {"fetched_url": "https://web.archive.org/web/2020id_/https://ex.com/article"},
    )
    live = (
        live_full,
        "text/html",
        {"snapshots": [], "final_url": "https://ex.com/article"},
    )
    with (
        _patch_fetchers(wayback_result=wb),
        patch("fetch.patchright_fetch.fetch", return_value=live),
    ):
        acquire("https://ex.com/article", tmp_path)
    assert (tmp_path / "asset.html").read_bytes() == live_full
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["fetch_method"] == "wayback+live"


def test_acquire_honors_explicit_wayback_url(tmp_path):
    # Given a specific archived snapshot, acquire fetches THAT capture and does
    # not re-fetch the live original (which for a dead URL redirects away).
    real = (
        b"<html><body>Clarke's Believe It or Not - the real interview</body></html>"
        * 30
    )
    wb_url = (
        "https://web.archive.org/web/20010405id_/"
        "http://www.space.com/peopleinterviews/clarke_believe_010227.html"
    )
    with (
        patch(
            "fetch.wayback.fetch_snapshot",
            return_value=(real, "text/html", {"fetched_url": wb_url}),
        ),
        patch("fetch.patchright_fetch.fetch") as mock_live,
    ):
        exit_code = acquire(wb_url, tmp_path)
    assert exit_code == 0
    assert (tmp_path / "asset.html").read_bytes() == real
    mock_live.assert_not_called()
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["fetch_method"] == "wayback-snapshot"


def test_acquire_rejects_primary_capture_redirected_away(tmp_path):
    # patchright as the primary fetcher (no wayback) landing on a generic page
    # after a redirect off the requested article -> rejected, acquire fails clean.
    landing = b"<html><body>generic section landing page content</body></html>" * 30
    fetchers = [
        ("wayback", lambda u: None),
        (
            "patchright",
            lambda u: (
                landing,
                "text/html",
                {"snapshots": [], "final_url": "https://x.com/news"},
            ),
        ),
    ]
    with patch("acquire.FETCHERS", fetchers):
        exit_code = acquire("https://x.com/articles/dead-piece", tmp_path)
    assert exit_code == 1
    assert not list(tmp_path.glob("asset.*"))
