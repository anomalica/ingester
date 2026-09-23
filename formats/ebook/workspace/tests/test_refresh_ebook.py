import json

import yaml

import ingest_ebook
from record3 import finalise_handler_record


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
    source = tmp_path / "fixture.epub"
    if not source.exists():
        _epub(source)
    staging = tmp_path / name
    staging.mkdir()
    (staging / "asset.epub").write_bytes(source.read_bytes())
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
    first = _staging(tmp_path, "first")
    assert ingest_ebook.run(first, output, force=False) == 0
    records = list((output / "store").glob("*.md"))
    assert len(records) == 1
    record = finalise_handler_record(
        records[0], first / "asset.epub", first / "manifest.json", output
    ).record_path
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
    from pipeline_version import current_version

    frontmatter = yaml.safe_load(after.split("---", 2)[1])
    assert frontmatter["processing"]["asset_pipeline_versions"][0][
        "pipeline_version"
    ] == current_version("ebook")
    assert not (output / "store" / "v1").exists()
