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
