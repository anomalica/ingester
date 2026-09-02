import hashlib
import json
from unittest.mock import patch

from extraction.trafilatura_ext import Article

import ingest_webpage
from test_refresh_fixtures import FRESH_BODY, FRONTMATTER, OLD_BODY


def _store(tmp_path, source_bytes):
    store = tmp_path / "output" / "store"
    store.mkdir(parents=True)
    h = "a" * 64
    sh = hashlib.sha256(source_bytes).hexdigest()
    record = store / f"{h}.md"
    record.write_text(f"---\n{FRONTMATTER.format(h=h, sh=sh)}\n---\n{OLD_BODY}")
    return store, record


@patch("ingest_webpage.extract_article")
def test_run_refreshes_in_place_when_the_page_bytes_are_already_ingested(
    mock_extract, tmp_path
):
    page = b"<html><body><p>Article</p></body></html>"
    store, record = _store(tmp_path, page)
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "asset.html").write_bytes(page)
    (staging / "manifest.json").write_text(
        json.dumps(
            {
                "source": str(tmp_path / "archived.html"),
                "asset": "asset.html",
                "detected_type": "text/html",
                "fetch_method": "local",
                "fetched_at": "2026-09-02T10:00:00Z",
                "source_url": "https://www.liberationtimes.com/home/late-officer",
            }
        )
    )
    mock_extract.return_value = Article(
        text=FRESH_BODY,
        title="Late Officer",
        authors=None,
        date="2026-04-24",
        sitename="Liberation Times",
        description=None,
    )
    assert ingest_webpage.run(staging, tmp_path / "output", force=False) == 0
    assert "**Burlison" in record.read_text()  # untouched without --force
    assert ingest_webpage.run(staging, tmp_path / "output", force=True) == 0
    assert list(store.glob("*.md")) == [record]
    assert "“grave concerns” that the death appears" in record.read_text()
