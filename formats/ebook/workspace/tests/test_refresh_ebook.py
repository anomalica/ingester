import json

import ingest_ebook


def _epub(path, body_one="Body one."):
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("urn:isbn:9780000000001")
    book.set_title("My Book")
    book.add_author("A. Writer")
    ch = epub.EpubHtml(title="1. First Chapter", file_name="c1.xhtml")
    ch.content = f"<html><body><h1>1. First Chapter</h1><p>{body_one}</p><p>Second paragraph of the chapter here.</p></body></html>"
    book.add_item(ch)
    book.toc = (ch,)
    book.spine = [ch]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)
    return path


def _staging(tmp_path, name):
    staging = tmp_path / name
    staging.mkdir()
    _epub(staging / "asset.epub")
    (staging / "manifest.json").write_text(
        json.dumps(
            {
                "source": str(staging / "asset.epub"),
                "asset": "asset.epub",
                "fetched_at": "2026-09-02T00:00:00Z",
            }
        )
    )
    return staging


def test_reingesting_the_same_epub_refreshes_the_record_in_place(tmp_path):
    output = tmp_path / "output"
    assert ingest_ebook.run(_staging(tmp_path, "first"), output, force=False) == 0
    records = list((output / "store").glob("*.md"))
    assert len(records) == 1
    record = records[0]
    before = record.read_text()
    # A human edit to the body must survive a forced re-extraction.
    record.write_text(
        before.replace(
            "Second paragraph of the chapter here.",
            "<!-- irrelevant: start -->\n\nSecond paragraph of the chapter here.\n\n<!-- irrelevant: end -->",
        )
    )

    assert ingest_ebook.run(_staging(tmp_path, "second"), output, force=False) == 0
    assert list((output / "store").glob("*.md")) == [record]

    assert ingest_ebook.run(_staging(tmp_path, "third"), output, force=True) == 0
    assert list((output / "store").glob("*.md")) == [record]
    after = record.read_text()
    assert "<!-- irrelevant: start -->" in after
    assert "  pipeline_version: 3" in after
    assert not (output / "store" / "v1").exists()
