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
