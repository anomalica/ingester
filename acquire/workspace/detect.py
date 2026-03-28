"""Content type detection from HTTP headers, magic bytes, and file extensions."""

from __future__ import annotations

from pathlib import Path

MAGIC_SIGNATURES = [
    (b"%PDF-", "application/pdf"),
    (b"<!DOCTYPE", "text/html"),
    (b"<!doctype", "text/html"),
    (b"<html", "text/html"),
    (b"<HTML", "text/html"),
]

EXTENSION_MAP = {
    ".html": "text/html",
    ".htm": "text/html",
    ".xhtml": "application/xhtml+xml",
    ".pdf": "application/pdf",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".wav": "audio/wav",
    ".webm": "video/webm",
    ".ogg": "audio/ogg",
    ".epub": "application/epub+zip",
    ".mobi": "application/x-mobipocket-ebook",
}


def detect_from_headers(content_type: str | None) -> str | None:
    """Extract MIME type from an HTTP Content-Type header value."""
    if not content_type:
        return None
    mime = content_type.split(";")[0].strip().lower()
    return mime if mime else None


def detect_from_bytes(data: bytes) -> str | None:
    """Detect content type from magic bytes at the start of data."""
    stripped = data.lstrip()
    for signature, mime_type in MAGIC_SIGNATURES:
        if stripped[: len(signature)] == signature:
            return mime_type
    return None


def detect_from_extension(path: str | Path) -> str | None:
    """Detect content type from file extension."""
    ext = Path(path).suffix.lower()
    return EXTENSION_MAP.get(ext)


def detect(
    data: bytes | None = None,
    content_type_header: str | None = None,
    path: str | Path | None = None,
) -> str | None:
    """Detect content type using all available signals.

    Priority: Content-Type header > magic bytes > file extension.
    """
    result = detect_from_headers(content_type_header)
    if result:
        return result

    if data:
        result = detect_from_bytes(data)
        if result:
            return result

    if path:
        result = detect_from_extension(path)
        if result:
            return result

    return None
