from __future__ import annotations

import pytest

from margin_db.object_storage import ObjectStorageError, S3CompatibleObjectStore


class FakeClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3Client:
    def __init__(self, *, head_error_code: str | None = None) -> None:
        self.head_error_code = head_error_code
        self.created_bucket_args: dict | None = None

    def head_bucket(self, *, Bucket: str) -> None:
        if self.head_error_code is not None:
            raise FakeClientError(self.head_error_code)

    def create_bucket(self, **kwargs: object) -> None:
        self.created_bucket_args = dict(kwargs)


def test_s3_object_store_ensure_ready_creates_missing_bucket() -> None:
    client = FakeS3Client(head_error_code="NoSuchBucket")
    store = S3CompatibleObjectStore(bucket="margin-artifacts", client=client, region_name="us-east-1")

    store.ensure_ready()

    assert client.created_bucket_args == {"Bucket": "margin-artifacts"}


def test_s3_object_store_ensure_ready_preserves_non_default_region() -> None:
    client = FakeS3Client(head_error_code="404")
    store = S3CompatibleObjectStore(bucket="margin-artifacts", client=client, region_name="eu-west-1")

    store.ensure_ready()

    assert client.created_bucket_args == {
        "Bucket": "margin-artifacts",
        "CreateBucketConfiguration": {"LocationConstraint": "eu-west-1"},
    }


def test_s3_object_store_put_file_uploads_stream(tmp_path) -> None:
    class UploadClient(FakeS3Client):
        def __init__(self) -> None:
            super().__init__()
            self.uploaded: dict[str, object] | None = None

        def upload_fileobj(self, file_obj, bucket: str, key: str, ExtraArgs: dict[str, object]) -> None:
            self.uploaded = {"bucket": bucket, "key": key, "body": file_obj.read(), "extra_args": ExtraArgs}

    path = tmp_path / "source.md"
    path.write_bytes(b"Margin streams uploads.")
    client = UploadClient()
    store = S3CompatibleObjectStore(bucket="margin-artifacts", client=client)

    stored = store.put_file(path)

    assert client.uploaded is not None
    assert client.uploaded["bucket"] == "margin-artifacts"
    assert client.uploaded["body"] == b"Margin streams uploads."
    assert client.uploaded["extra_args"] == {"Metadata": {"sha256": stored.content_hash}}
    assert stored.size_bytes == path.stat().st_size


def test_s3_object_store_ensure_ready_rejects_inaccessible_bucket() -> None:
    client = FakeS3Client(head_error_code="AccessDenied")
    store = S3CompatibleObjectStore(bucket="margin-artifacts", client=client)

    with pytest.raises(ObjectStorageError):
        store.ensure_ready()

    assert client.created_bucket_args is None
