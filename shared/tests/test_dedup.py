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


def test_find_by_source_id_skips_superseded(tmp_path):
    """A superseded record is retired - it must not count as a live duplicate,
    so the live record with the same source_id is the one returned."""
    store = tmp_path / "store"
    _write_record(store, "old", source_id="calibre:4268", superseded_by="newhash")
    live = _write_record(store, "znew", source_id="calibre:4268")

    assert find_by_source_id(store, "calibre:4268") == live


def test_find_by_source_id_all_superseded_returns_none(tmp_path):
    """If the only record with this source_id is superseded, nothing matches -
    a re-ingest should be allowed to proceed."""
    store = tmp_path / "store"
    _write_record(store, "old", source_id="calibre:4268", superseded_by="newhash")

    assert find_by_source_id(store, "calibre:4268") is None


def test_missing_store_dir_returns_none(tmp_path):
    assert find_by_source_id(tmp_path / "no-store", "youtube:X") is None
    assert find_by_source_url(tmp_path / "no-store", "https://x") is None


def test_skips_files_without_frontmatter(tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    (store / "broken.md").write_text("no frontmatter here")
    expected = _write_record(store, "h1", source_id="youtube:X")

    assert find_by_source_id(store, "youtube:X") == expected


def test_finds_a_record_by_its_alias_url(tmp_path):
    """A merged record answers to every URL it was published at.

    The same recording is often uploaded twice - the publisher's own channel and
    a repost. Merging keeps ONE source_url and lists the rest under
    `also_published_at`; if dedup reads source_url alone, re-pasting the alias
    ingests it afresh and recreates the duplicate the merge removed.
    """
    store = tmp_path / "store"
    expected = _write_record(
        store,
        "h1",
        source_url="https://www.youtube.com/watch?v=CANON",
        fetched_url="https://www.youtube.com/watch?v=REPOST",
    )
    # A list-valued field cannot go through _write_record's key: value writer.
    text = expected.read_text().replace(
        "---\n\nbody",
        "also_published_at:\n  - https://www.youtube.com/watch?v=REPOST\n---\n\nbody",
    )
    expected.write_text(text)

    assert (
        find_by_source_url(store, "https://www.youtube.com/watch?v=CANON") == expected
    )
    assert (
        find_by_source_url(store, "https://www.youtube.com/watch?v=REPOST") == expected
    )
    assert find_by_source_url(store, "https://www.youtube.com/watch?v=OTHER") is None


def test_an_alias_on_a_superseded_record_does_not_block_reingest(tmp_path):
    """Aliases follow the same retirement rule as the record carrying them: a
    superseded record is not an existing copy, so neither are its aliases."""
    store = tmp_path / "store"
    path = _write_record(
        store,
        "h1",
        source_url="https://www.youtube.com/watch?v=CANON",
        superseded_by="deadbeef",
    )
    path.write_text(
        path.read_text().replace(
            "---\n\nbody",
            "also_published_at:\n  - https://www.youtube.com/watch?v=REPOST\n---\n\nbody",
        )
    )

    assert find_by_source_url(store, "https://www.youtube.com/watch?v=REPOST") is None


def test_finds_an_alias_inside_a_provenance_block(tmp_path):
    """Records exist in both shapes while decision 0043's provenance migration is
    in progress. Reading only the top level would drop a record's aliases the day
    it is migrated - silently, since dedup returning None looks like 'new source'."""
    store = tmp_path / "store"
    store.mkdir(parents=True)
    path = store / "h1.md"
    path.write_text(
        "---\n"
        "schema: anomalica/record/1\n"
        "provenance:\n"
        '  source_url: "https://www.youtube.com/watch?v=CANON"\n'
        "  also_published_at:\n"
        '    - "https://www.youtube.com/watch?v=REPOST"\n'
        "---\n\nbody\n"
    )

    assert find_by_source_url(store, "https://www.youtube.com/watch?v=REPOST") == path
