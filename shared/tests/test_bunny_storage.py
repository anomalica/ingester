import bunny_storage


def test_push_record_publishes_every_composite_asset_independently(
    tmp_path, monkeypatch
):
    record = tmp_path / "record.md"
    record.write_text("---\nschema: anomalica/record/3\n---\nbody\n")
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.html"
    first.write_bytes(b"pdf")
    second.write_bytes(b"html")
    assets = [
        ("a" * 64, "pdf", "restricted", "pdf"),
        ("b" * 64, "html", "public_domain", "web"),
    ]
    files = {"a" * 64: first, "b" * 64: second}
    puts = []
    stamped = []

    monkeypatch.setattr(bunny_storage, "archived_assets", lambda _: assets)
    monkeypatch.setattr(
        bunny_storage,
        "archived_storage_key",
        lambda _, hash_, ext: f"sources/{hash_}.{ext}",
    )
    monkeypatch.setattr(bunny_storage, "local_file", lambda hash_, _: files.get(hash_))
    monkeypatch.setattr(bunny_storage, "_sops", lambda _: "credential")
    monkeypatch.setattr(bunny_storage, "_zone_has", lambda *_: False)
    monkeypatch.setattr(
        bunny_storage,
        "_put",
        lambda zone, key, password, data, ext: puts.append(
            (zone, key, password, data, ext)
        )
        or 201,
    )
    monkeypatch.setattr(bunny_storage, "_stamp", lambda *args: stamped.append(args))

    assert bunny_storage.push_record(record) == "pushed"
    assert puts == [
        (
            "anomalica-gated",
            f"sources/{'a' * 64}.pdf",
            "credential",
            b"pdf",
            "pdf",
        ),
        (
            "anomalica-wb",
            f"sources/{'b' * 64}.html",
            "credential",
            b"html",
            "html",
        ),
    ]
    assert stamped == []
