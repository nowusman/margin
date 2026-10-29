from __future__ import annotations

import pytest

from margin_db.object_storage import FilesystemObjectStore, ObjectNotFoundError, sha256_bytes


def test_filesystem_store_uploads_and_retrieves_raw_artifact_by_content_hash(tmp_path) -> None:
    store = FilesystemObjectStore(tmp_path / "objects")
    artifact = b"%PDF-1.7\nraw margin artifact bytes\n"

    stored = store.put_bytes(artifact)

    assert stored.content_hash == sha256_bytes(artifact)
    assert stored.size_bytes == len(artifact)
    assert stored.uri.startswith("file:")
    assert store.exists(stored.content_hash)
    assert store.get_bytes(stored.content_hash) == artifact


def test_filesystem_store_is_content_addressed_across_repeated_uploads(tmp_path) -> None:
    store = FilesystemObjectStore(tmp_path / "objects")
    artifact = b"same bytes, same content hash"

    first = store.put_bytes(artifact)
    second = store.put_bytes(artifact)

    assert first == second
    assert store.get_bytes(first.content_hash) == artifact


def test_filesystem_store_writes_download_to_file(tmp_path) -> None:
    store = FilesystemObjectStore(tmp_path / "objects")
    artifact = b"markdown or office-document bytes"
    stored = store.put_bytes(artifact)

    destination = store.get_to_file(stored.content_hash, tmp_path / "downloads" / "artifact.bin")

    assert destination.read_bytes() == artifact


def test_filesystem_store_raises_for_missing_hash(tmp_path) -> None:
    store = FilesystemObjectStore(tmp_path / "objects")
    missing_hash = "0" * 64

    with pytest.raises(ObjectNotFoundError):
        store.get_bytes(missing_hash)
