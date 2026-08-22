"""Bare image inputs to the document handler.

A photographed or scanned document arrives as an image; it is hashed and archived
as the original bytes (same class as a scanned PDF) and only a bounded copy is
handed to the vision model. These tests pin the acceptance set, the passthrough of
in-bounds images, and the downscale of oversized ones - never the archived source.
"""

import pymupdf

from extraction.images import (
    MODEL_IMAGE_MAX_PX,
    data_uri,
    is_image,
    model_image,
)


def _make_image(path, w, h, fill=210):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, w, h))
    pix.clear_with(fill)
    pix.save(str(path))
    return path


def test_is_image_accepts_v1_set_only():
    assert is_image("scan.jpg")
    assert is_image("shot.JPEG")
    assert is_image("a.png")
    assert is_image("b.webp")
    # not accepted in v1 (browser cannot render tiff/heic; pdf is not an image)
    assert not is_image("doc.pdf")
    assert not is_image("phone.heic")
    assert not is_image("fax.tiff")


def test_model_image_passes_a_small_png_through_untouched(tmp_path):
    p = _make_image(tmp_path / "small.png", 100, 100)
    data, media_type = model_image(p)
    assert media_type == "image/png"
    assert data == p.read_bytes(), (
        "an in-bounds passthrough format must not be re-encoded"
    )


def test_model_image_passes_a_small_jpg_through_untouched(tmp_path):
    p = _make_image(tmp_path / "small.jpg", 120, 90)
    data, media_type = model_image(p)
    assert media_type == "image/jpeg"
    assert data == p.read_bytes()


def test_model_image_downscales_an_oversized_image_to_bounded_png(tmp_path):
    p = _make_image(tmp_path / "huge.png", MODEL_IMAGE_MAX_PX + 1800, 3000)
    data, media_type = model_image(p)
    assert media_type == "image/png"
    out = pymupdf.open(stream=data, filetype="png")
    pix = out[0].get_pixmap()
    assert max(pix.width, pix.height) <= MODEL_IMAGE_MAX_PX
    out.close()


def test_data_uri_is_a_base64_image_uri(tmp_path):
    p = _make_image(tmp_path / "slide.jpg", 80, 80)
    uri = data_uri(p)
    assert uri.startswith("data:image/jpeg;base64,")
