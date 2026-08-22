"""Bare image inputs routed through the document (PDF) vision handler.

A photographed or scanned document arrives as an image (jpg/png/webp). The image
is the acquisition format, not the work - a single-page scan and a 400-page
scanned PDF differ only in page count - so images extract through this handler's
vision path rather than a separate container. The ORIGINAL image is the archived
source and is hashed as the source bytes (content_hash), the same hash class as a
scanned PDF; only a bounded, model-acceptable copy is produced here for the vision
call, never stored.

v1 accepts browser-renderable formats only. TIFF and HEIC are held back: a browser
cannot draw either, so the workbench would show the reviewer nothing, and they need
a decoded-PNG display derivative (a spec question) before they can be accepted.
HEIC additionally needs a container decode check, since pymupdf is not always built
with libheif.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pymupdf

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# Media types the vision APIs accept directly, so an in-bounds image of one passes
# through untouched (full fidelity, no re-encode).
_PASSTHROUGH = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

# A phone photo at full resolution blows the vision image-token budget and costs
# more without helping legibility. Bound the longest side; a document at ~2200 px
# keeps small print readable. The archived source is untouched - this bound applies
# only to the copy handed to the model.
MODEL_IMAGE_MAX_PX = int(os.environ.get("INGEST_IMAGE_MAX_PX", "2200"))


def is_image(path: str | Path) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


def model_image(path: str | Path) -> tuple[bytes, str]:
    """A model-acceptable copy of the image as (bytes, media_type).

    An in-bounds image in a passthrough format is returned as its original bytes;
    an oversized image is decoded and downscaled to PNG. Raises if the image cannot
    be decoded (a format the container's pymupdf was not built to read)."""
    path = Path(path)
    ext = path.suffix.lower()
    doc = pymupdf.open(str(path))
    try:
        if doc.page_count < 1:
            raise RuntimeError(f"no decodable image in {path.name}")
        page = doc[0]
        pix = page.get_pixmap()
        longest = max(pix.width, pix.height)
        media_type = _PASSTHROUGH.get(ext)
        if media_type and longest <= MODEL_IMAGE_MAX_PX:
            return path.read_bytes(), media_type
        if longest > MODEL_IMAGE_MAX_PX:
            scale = MODEL_IMAGE_MAX_PX / longest
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
        return pix.tobytes("png"), "image/png"
    finally:
        doc.close()


def data_uri(path: str | Path) -> str:
    data, media_type = model_image(path)
    return f"data:{media_type};base64," + base64.b64encode(data).decode()
