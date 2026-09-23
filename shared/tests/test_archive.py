import hashlib
from concurrent.futures import ThreadPoolExecutor

import pytest

from archive import ArchiveError, archive_content_addressed


def test_concurrent_archive_writers_publish_one_complete_object(tmp_path):
    data = b"immutable acquired bytes" * 4096
    digest = hashlib.sha256(data).hexdigest()
    source = tmp_path / "source.pdf"
    source.write_bytes(data)
    target = tmp_path / "records" / f"{digest}.pdf"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(
            executor.map(
                lambda _: archive_content_addressed(source, target, digest), range(2)
            )
        )

    assert sorted(outcomes) == [False, True]
    assert target.read_bytes() == data
    assert not list(target.parent.glob(f".{target.name}.*"))


def test_archive_refuses_corrupt_existing_object(tmp_path):
    data = b"expected bytes"
    digest = hashlib.sha256(data).hexdigest()
    source = tmp_path / "source.html"
    source.write_bytes(data)
    target = tmp_path / f"{digest}.html"
    target.write_bytes(b"corrupt")

    with pytest.raises(ArchiveError, match="content-addressed path"):
        archive_content_addressed(source, target, digest)


def test_archive_refuses_symlink_at_content_addressed_target(tmp_path):
    data = b"expected bytes"
    digest = hashlib.sha256(data).hexdigest()
    source = tmp_path / "source.html"
    source.write_bytes(data)
    target = tmp_path / f"{digest}.html"
    target.symlink_to(source)

    with pytest.raises(ArchiveError, match="content-addressed path"):
        archive_content_addressed(source, target, digest)
