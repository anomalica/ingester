from pathlib import Path

from dedup import find_by_source_id, find_by_source_url


def _write_record(store: Path, hex_hash: str, **frontmatter: str) -> Path:
    store.mkdir(parents=True, exist_ok=True)
    path = store / f"{hex_hash}.md"
    lines = ["---", "schema: anomalica/record/1"]
    for key, value in frontmatter.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append("body")
    path.write_text("\n".join(lines))
    return path


def test_find_by_source_id_match(tmp_path):
    store = tmp_path / "store"
    expected = _write_record(
        store, "h1", source_id="youtube:ABC123", source_url="https://youtu.be/ABC123"
    )
    _write_record(store, "h2", source_id="youtube:OTHER")

    assert find_by_source_id(store, "youtube:ABC123") == expected


def test_find_by_source_id_no_match(tmp_path):
    store = tmp_path / "store"
    _write_record(store, "h1", source_id="youtube:ABC123")

    assert find_by_source_id(store, "youtube:DIFFERENT") is None


def test_find_by_source_id_empty_query(tmp_path):
    store = tmp_path / "store"
    _write_record(store, "h1", source_id="youtube:ABC123")

    assert find_by_source_id(store, "") is None


def test_find_by_source_url_match(tmp_path):
    store = tmp_path / "store"
    url = "https://example.com/article"
    expected = _write_record(store, "h1", source_url=url)
    _write_record(store, "h2", source_url="https://example.com/other")

    assert find_by_source_url(store, url) == expected


def test_find_by_source_url_exact_only(tmp_path):
    """No URL normalisation - trailing slash is treated as a different URL."""
    store = tmp_path / "store"
    _write_record(store, "h1", source_url="https://example.com/article")

    assert find_by_source_url(store, "https://example.com/article/") is None


def test_find_by_source_url_missing_field(tmp_path):
    """Records without a source_url field are skipped, not matched."""
    store = tmp_path / "store"
    _write_record(store, "h1", title="Local PDF")

    assert find_by_source_url(store, "https://example.com/article") is None


def test_missing_store_dir_returns_none(tmp_path):
    assert find_by_source_id(tmp_path / "no-store", "youtube:X") is None
    assert find_by_source_url(tmp_path / "no-store", "https://x") is None


def test_skips_files_without_frontmatter(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    (store / "broken.md").write_text("no frontmatter here")
    expected = _write_record(store, "h1", source_id="youtube:X")

    assert find_by_source_id(store, "youtube:X") == expected
