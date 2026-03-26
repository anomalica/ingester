from hashing import (
    hash_bytes,
    hash_string,
    hash_file,
    content_hash_label,
    store_path,
    store_exists,
)


def test_hash_bytes_deterministic():
    assert hash_bytes(b"hello") == hash_bytes(b"hello")


def test_hash_bytes_differs_for_different_input():
    assert hash_bytes(b"hello") != hash_bytes(b"world")


def test_hash_string_uses_utf8():
    assert hash_string("hello") == hash_bytes(b"hello")


def test_hash_file(tmp_path):
    f = tmp_path / "test.txt"
    f.write_bytes(b"hello")
    assert hash_file(f) == hash_bytes(b"hello")


def test_content_hash_label():
    assert content_hash_label("abc123") == "sha256:abc123"


def test_store_path(tmp_path):
    p = store_path(tmp_path, "abc123")
    assert p == tmp_path / "abc123.md"


def test_store_path_custom_suffix(tmp_path):
    p = store_path(tmp_path, "abc123", ".meta.json")
    assert p == tmp_path / "abc123.meta.json"


def test_store_exists_false(tmp_path):
    assert store_exists(tmp_path, "abc123") is False


def test_store_exists_true(tmp_path):
    (tmp_path / "abc123.md").write_text("content")
    assert store_exists(tmp_path, "abc123") is True
