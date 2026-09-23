from regenerate_snapshots import regenerate


def test_legacy_snapshot_editor_leaves_record3_untouched(tmp_path, monkeypatch):
    path = tmp_path / "record.md"
    original = (
        "---\n"
        "schema: anomalica/record/3\n"
        "content_hash: sha256:" + "a" * 64 + "\nsource_type: web\n"
        "---\nBody.\n"
    )
    path.write_text(original)
    monkeypatch.setattr(
        "regenerate_snapshots.capture",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("captured")),
    )

    result = regenerate(path, write=True, from_url="https://example.test")

    assert result == "record/3 snapshots refresh through ./ingest --force - left alone"
    assert path.read_text() == original
