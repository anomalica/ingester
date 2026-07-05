from extraction.images import (
    HarvestedImage,
    _ext_for,
    render_images,
)


def _ok_fetch(data=b"imagebytes", content_type="image/png"):
    def fetch(url):
        return (data + url.encode(), content_type)

    return fetch


def _fail_fetch(url):
    return None


IMG = "https://ex.com/a.png"


def test_render_emits_block_annotation_with_file():
    md = f"![the alt]({IMG})"
    harvested = [HarvestedImage(url=IMG, alt="the alt", caption=None)]
    text, media = render_images(md, harvested, fetch=_ok_fetch())
    assert "![" not in text  # no residual markdown image
    assert "<!--\nimage:" in text
    assert f"  file: {media[0].img_hash}.png" in text
    assert '  alt: "the alt"' in text
    assert len(media) == 1


def test_caption_from_trailing_italic_line_and_removed_from_body():
    md = f"![]({IMG})\n\n*Jane Doe (Copyright (c) J. Doe. Do not reproduce.)*\n\nReal prose after."
    harvested = [HarvestedImage(url=IMG, alt=None, caption=None)]
    text, media = render_images(md, harvested, fetch=_ok_fetch())
    assert '  caption: "Jane Doe (Copyright (c) J. Doe. Do not reproduce.)"' in text
    # the italic caption line must not remain as loose body prose
    assert "*Jane Doe" not in text
    assert "Real prose after." in text


def test_figcaption_caption_wins_over_trailing_italic():
    md = f"![]({IMG})\n\n*loose italic that should be ignored*"
    harvested = [HarvestedImage(url=IMG, alt=None, caption="Figcaption caption")]
    text, _ = render_images(md, harvested, fetch=_ok_fetch())
    assert '  caption: "Figcaption caption"' in text


def test_download_failure_emits_annotation_without_file():
    md = f"![alt text]({IMG})"
    harvested = [HarvestedImage(url=IMG, alt="alt text", caption="a caption")]
    text, media = render_images(md, harvested, fetch=_fail_fetch)
    assert media == []
    assert "  file:" not in text
    assert '  alt: "alt text"' in text
    assert '  caption: "a caption"' in text


def test_distinct_bytes_give_distinct_hashes():
    md = f"![]({IMG})\n\n![](https://ex.com/b.png)"
    harvested = [
        HarvestedImage(url=IMG, alt=None, caption="one"),
        HarvestedImage(url="https://ex.com/b.png", alt=None, caption="two"),
    ]
    _, media = render_images(md, harvested, fetch=_ok_fetch())
    assert len(media) == 2
    assert media[0].img_hash != media[1].img_hash


def test_repeated_url_emitted_once():
    md = f"![]({IMG})\n\n![]({IMG})"
    harvested = [HarvestedImage(url=IMG, alt=None, caption="cap")]
    text, media = render_images(md, harvested, fetch=_ok_fetch())
    assert text.count("<!--\nimage:") == 1
    assert len(media) == 1


def test_dropped_images_appended_as_annotations():
    md = "Just article prose, no inline images."
    harvested = [HarvestedImage(url=IMG, alt="hero", caption="hero caption")]
    text, media = render_images(md, harvested, fetch=_ok_fetch())
    assert "<!--\nimage:" in text
    assert '  alt: "hero"' in text
    assert len(media) == 1


def test_ext_for_prefers_content_type_then_url():
    assert _ext_for("image/jpeg", "https://x/a") == "jpg"
    assert _ext_for("image/jpeg; charset=binary", "https://x/a") == "jpg"
    assert _ext_for(None, "https://x/a.webp?v=2") == "webp"
    assert _ext_for(None, "https://x/a.JPEG") == "jpg"
    assert _ext_for(None, "https://x/no-extension") == "jpg"
