import re

from extraction.images import (
    HarvestedImage,
    _ext_for,
    _likely_chrome_url,
    harvest_images,
    render_images,
)


def test_social_media_icons_filtered_from_harvest():
    # social-share plugin icons must not be captured as article images
    assert _likely_chrome_url(
        "https://x/plugins/social-media-buttons-toolbar/img/social-media-icons/discord.png"
    )
    assert not _likely_chrome_url("https://x/wp-content/uploads/2023/06/photo.jpg")
    html = (
        "<html><body><article>"
        "<p>Article prose with enough words to be a real content region here.</p>"
        '<img src="https://x/social-media-icons/tiktok.png" alt="tiktok">'
        '<figure><img src="https://x/uploads/real-photo.jpg" alt="a real photo">'
        "</figure></article></body></html>"
    )
    urls = [img.url for img in harvest_images(html)]
    assert "https://x/uploads/real-photo.jpg" in urls
    assert not any("tiktok" in u for u in urls)


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


def test_plain_caption_duplicating_figcaption_is_consumed():
    # trafilatura emits a figcaption as a plain prose line too; it must not
    # remain in the body when it is already the annotation's caption.
    cap = "Karl E. Nell (Credit: Department of the Army)"
    md = f"![]({IMG})\n\n{cap}\n\nReal article prose continues here."
    harvested = [HarvestedImage(url=IMG, alt=None, caption=cap)]
    text, _ = render_images(md, harvested, fetch=_ok_fetch())
    body_no_annot = re.sub(r"<!--\nimage:.*?-->", "", text, flags=re.DOTALL)
    assert "Karl E. Nell" not in body_no_annot  # not loose in prose
    assert f'  caption: "{cap}"' in text  # in the annotation
    assert "Real article prose continues here." in text


def test_plain_line_not_matching_caption_is_kept_as_prose():
    # a plain line after the image that is NOT the caption stays as prose.
    md = f"![]({IMG})\n\nThis is a real paragraph, not the caption.\n"
    harvested = [HarvestedImage(url=IMG, alt=None, caption="A different caption")]
    text, _ = render_images(md, harvested, fetch=_ok_fetch())
    assert "This is a real paragraph, not the caption." in text


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


def _fetch_png(url):
    return (b"\x89PNG bytes " + url.encode(), "image/png")


def test_lead_image_with_no_alt_or_caption_leads_the_body():
    # A Squarespace post's lead picture: no alt, no figcaption, before all text.
    html = """<html><body><article>
    <h1>Lue Elizondo: No Going Back</h1>
    <figure><img src="https://cdn.example/lue.jpg" alt="" data-image-dimensions="916x1191"></figure>
    <p>Written by Christopher Sharp - 3 June 2026</p>
    <p>Lue Elizondo has said there is no going back on disclosure.</p>
    </article></body></html>"""
    md = "Written by Christopher Sharp - 3 June 2026\n\nLue Elizondo has said there is no going back on disclosure.\n"
    text, media = render_images(md, harvest_images(html), fetch=_fetch_png)
    assert text.startswith("<!--\nimage:\n  file: ")
    assert len(media) == 1
    assert text.index("image:") < text.index("Written by")


def test_dropped_image_goes_back_after_the_paragraph_it_followed():
    html = """<html><body><article>
    <p>First paragraph of the article, long enough to anchor.</p>
    <figure><img src="https://cdn.example/mid.jpg" alt="" width="800" height="600"></figure>
    <p>Second paragraph continues the story afterwards.</p>
    </article></body></html>"""
    md = "First paragraph of the article, long enough to anchor.\n\nSecond paragraph continues the story afterwards.\n"
    text, _ = render_images(md, harvest_images(html), fetch=_fetch_png)
    first, second = text.index("First paragraph"), text.index("Second paragraph")
    assert first < text.index("<!--\nimage:") < second


def test_tiny_images_are_not_harvested():
    html = """<html><body><article>
    <p>Some article text that is long enough.</p>
    <img src="https://cdn.example/avatar.jpg" width="32" height="32">
    <img src="https://cdn.example/pixel.gif" data-image-dimensions="1x1">
    <img src="https://cdn.example/real.jpg" width="1200" height="800">
    </article></body></html>"""
    urls = [h.url for h in harvest_images(html)]
    assert urls == ["https://cdn.example/real.jpg"]


def test_dropped_image_that_cannot_be_fetched_and_says_nothing_is_left_out():
    html = """<html><body><article>
    <p>Some article text that is long enough.</p>
    <img src="https://cdn.example/gone.jpg" width="1200" height="800">
    </article></body></html>"""
    text, _ = render_images(
        "Some article text that is long enough.\n",
        harvest_images(html),
        fetch=lambda url: None,
    )
    assert "image:" not in text


def test_image_after_text_the_extractor_rejected_is_rejected_with_it():
    html = """<html><body><article>
    <p>Real article prose that is long enough to anchor.</p>
    <p>Love our content and wish to support the website?</p>
    <img src="https://cdn.example/banner.jpg" width="1200" height="400">
    </article></body></html>"""
    md = "Real article prose that is long enough to anchor.\n"
    text, media = render_images(md, harvest_images(html), fetch=_fetch_png)
    assert "image:" not in text and media == []


def test_lead_picture_is_placed_by_the_caption_that_follows_it_not_the_header_author_link():
    # Squarespace repeats the author above the title; the byline is body text
    # below the picture. The picture goes before its caption, and the caption
    # trafilatura left as loose prose folds into the annotation.
    html = """<html><body><article>
    <a href="/author">Christopher Sharp</a>
    <h1>Newly Released Records Reveal Drone Incursions</h1>
    <figure><img src="https://cdn.example/columbia.png" alt="" data-image-dimensions="1726x883">
    <figcaption><em>Above: Columbia Generating Station, Washington</em></figcaption></figure>
    <p>Written by <a href="/x">Kyle Warfel</a> and <a href="/y">Christopher Sharp</a> - 8 April 2026</p>
    <p>Liberation Times has obtained records detailing drone incidents.</p>
    </article></body></html>"""
    md = (
        "Above: Columbia Generating Station, Washington\n\n"
        "Written by [Kyle Warfel](/x) and [Christopher Sharp](/y) - 8 April 2026\n\n"
        "Liberation Times has obtained records detailing drone incidents.\n"
    )
    text, media = render_images(md, harvest_images(html), fetch=_fetch_png)
    assert text.startswith("<!--\nimage:\n  file: ")
    assert '  caption: "Above: Columbia Generating Station, Washington"' in text
    assert text.count("Above: Columbia Generating Station") == 1
    assert text.index("-->") < text.index("Written by")


def test_a_banner_whose_only_anchor_is_a_name_in_the_byline_is_left_out():
    html = """<html><body><article>
    <p>Written by <a href="/y">Christopher Sharp</a> - 8 April 2026</p>
    <p>Liberation Times has obtained records detailing drone incidents.</p>
    <p>Love our content and wish to support the website? donate through PayPal</p>
    <img src="https://cdn.example/banner.jpg" width="1200" height="400">
    <a href="/author"><strong>Christopher Sharp</strong></a>
    </article></body></html>"""
    md = (
        "Written by [Christopher Sharp](/y) - 8 April 2026\n\n"
        "Liberation Times has obtained records detailing drone incidents.\n"
    )
    text, media = render_images(md, harvest_images(html), fetch=_fetch_png)
    assert "image:" not in text and media == []
