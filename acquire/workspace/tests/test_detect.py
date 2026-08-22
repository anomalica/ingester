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


def test_detect_from_extension_image():
    """A photographed/scanned document arrives as an image and routes to the PDF
    (document) handler. A local reprocess detects by extension with no HTTP header,
    so each accepted image extension must resolve to its image MIME type."""
    assert detect_from_extension("slide.jpg") == "image/jpeg"
    assert detect_from_extension("a.jpeg") == "image/jpeg"
    assert detect_from_extension("shot.PNG") == "image/png"  # case-insensitive
    assert detect_from_extension("x.webp") == "image/webp"


def test_detect_from_bytes_jpeg():
    assert detect_from_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00") == "image/jpeg"


def test_detect_from_bytes_png():
    assert detect_from_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR") == "image/png"


def test_detect_from_extension_archived_audio():
    """Every extension the pipeline archives media under must detect as audio/video,
    not fall through to octet-stream. yt-dlp writes .opus by default, so a local
    reprocess of an archived source (which detects by EXTENSION, no HTTP header)
    silently failed at acquire until .opus/.m4a/.oga were added beside .ogg."""
    assert detect_from_extension("asset.opus") == "audio/ogg"
    assert detect_from_extension("asset.oga") == "audio/ogg"
    assert detect_from_extension("asset.m4a") == "audio/mp4"
    assert detect_from_extension("ASSET.OPUS") == "audio/ogg"  # case-insensitive


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
