from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class ObjectStorageError(RuntimeError):
    """Base exception for Margin object storage failures."""


class ObjectNotFoundError(ObjectStorageError):
    """Raised when a content-addressed object is missing."""


class ObjectIntegrityError(ObjectStorageError):
    """Raised when stored bytes do not match their content hash."""


@dataclass(frozen=True)
class StoredObject:
    content_hash: str
    size_bytes: int
    uri: str


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_content_hash(content_hash: str) -> str:
    normalized = content_hash.lower()
    if not HASH_RE.fullmatch(normalized):
        raise ValueError("content hash must be a 64-character lowercase sha256 hex digest")
    return normalized


class ContentAddressableObjectStore(ABC):
    """Small content-addressed storage API for raw Margin artifacts."""

    def ensure_ready(self) -> None:
        """Ensure the backing store is ready for reads and writes."""
        return None

    @abstractmethod
    def put_bytes(self, data: bytes) -> StoredObject:
        """Store bytes and return their sha256-addressed object metadata."""

    @abstractmethod
    def get_bytes(self, content_hash: str) -> bytes:
        """Retrieve bytes by sha256 content hash."""

    @abstractmethod
    def exists(self, content_hash: str) -> bool:
        """Return whether an object exists for the supplied content hash."""

    def put_file(self, path: str | Path) -> StoredObject:
        return self.put_bytes(Path(path).read_bytes())

    def get_to_file(self, content_hash: str, destination: str | Path) -> Path:
        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        data = self.get_bytes(content_hash)

        fd, temp_name = tempfile.mkstemp(
            prefix=f".{destination_path.name}.", suffix=".tmp", dir=destination_path.parent
        )
        try:
            with os.fdopen(fd, "wb") as temp_file:
                temp_file.write(data)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_name, destination_path)
        except Exception:
            Path(temp_name).unlink(missing_ok=True)
            raise

        return destination_path


class FilesystemObjectStore(ContentAddressableObjectStore):
    """Filesystem-backed content store used for local tests and development."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, data: bytes) -> StoredObject:
        content_hash = sha256_bytes(data)
        object_path = self._path_for_hash(content_hash)
        object_path.parent.mkdir(parents=True, exist_ok=True)

        if not object_path.exists():
            fd, temp_name = tempfile.mkstemp(prefix=f".{content_hash}.", suffix=".tmp", dir=object_path.parent)
            try:
                with os.fdopen(fd, "wb") as temp_file:
                    temp_file.write(data)
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
                os.replace(temp_name, object_path)
            except Exception:
                Path(temp_name).unlink(missing_ok=True)
                raise

        return StoredObject(content_hash=content_hash, size_bytes=len(data), uri=object_path.as_uri())

    def put_file(self, path: str | Path) -> StoredObject:
        source_path = Path(path)
        content_hash = sha256_file(source_path)
        object_path = self._path_for_hash(content_hash)
        object_path.parent.mkdir(parents=True, exist_ok=True)

        if not object_path.exists():
            fd, temp_name = tempfile.mkstemp(prefix=f".{content_hash}.", suffix=".tmp", dir=object_path.parent)
            try:
                with os.fdopen(fd, "wb") as temp_file, source_path.open("rb") as source_file:
                    shutil.copyfileobj(source_file, temp_file, length=1024 * 1024)
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
                os.replace(temp_name, object_path)
            except Exception:
                Path(temp_name).unlink(missing_ok=True)
                raise

        return StoredObject(content_hash=content_hash, size_bytes=source_path.stat().st_size, uri=object_path.as_uri())

    def get_bytes(self, content_hash: str) -> bytes:
        normalized_hash = validate_content_hash(content_hash)
        object_path = self._path_for_hash(normalized_hash)
        if not object_path.exists():
            raise ObjectNotFoundError(f"object not found: {normalized_hash}")

        data = object_path.read_bytes()
        actual_hash = sha256_bytes(data)
        if actual_hash != normalized_hash:
            raise ObjectIntegrityError(f"stored object hash mismatch: expected {normalized_hash}, got {actual_hash}")
        return data

    def exists(self, content_hash: str) -> bool:
        normalized_hash = validate_content_hash(content_hash)
        return self._path_for_hash(normalized_hash).exists()

    def _path_for_hash(self, content_hash: str) -> Path:
        normalized_hash = validate_content_hash(content_hash)
        return self.root / normalized_hash[:2] / normalized_hash[2:4] / normalized_hash


class S3CompatibleObjectStore(ContentAddressableObjectStore):
    """Optional boto3-backed adapter for S3-compatible stores such as MinIO."""

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "raw",
        client: Any | None = None,
        endpoint_url: str | None = None,
        region_name: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        aws_session_token: str | None = None,
    ) -> None:
        if client is None:
            try:
                import boto3  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError(
                    "boto3 is required for S3CompatibleObjectStore; " "use FilesystemObjectStore for local tests"
                ) from exc
            client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                region_name=region_name,
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
                aws_session_token=aws_session_token,
            )

        self.client = client
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.region_name = region_name

    def ensure_ready(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return
        except Exception as exc:
            if not _is_missing_bucket_error(exc):
                raise ObjectStorageError(f"object store bucket is not accessible: {self.bucket}") from exc

        try:
            self.client.create_bucket(**self._create_bucket_kwargs())
        except Exception as exc:
            if _is_bucket_already_owned_error(exc):
                return
            raise ObjectStorageError(f"object store bucket could not be created: {self.bucket}") from exc

    def put_bytes(self, data: bytes) -> StoredObject:
        content_hash = sha256_bytes(data)
        key = self._key_for_hash(content_hash)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, Metadata={"sha256": content_hash})
        return StoredObject(content_hash=content_hash, size_bytes=len(data), uri=f"s3://{self.bucket}/{key}")

    def put_file(self, path: str | Path) -> StoredObject:
        source_path = Path(path)
        content_hash = sha256_file(source_path)
        key = self._key_for_hash(content_hash)
        with source_path.open("rb") as source_file:
            self.client.upload_fileobj(source_file, self.bucket, key, ExtraArgs={"Metadata": {"sha256": content_hash}})
        return StoredObject(
            content_hash=content_hash, size_bytes=source_path.stat().st_size, uri=f"s3://{self.bucket}/{key}"
        )

    def get_bytes(self, content_hash: str) -> bytes:
        normalized_hash = validate_content_hash(content_hash)
        key = self._key_for_hash(normalized_hash)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise ObjectNotFoundError(f"object not found: {normalized_hash}") from exc

        body = response["Body"].read()
        actual_hash = sha256_bytes(body)
        if actual_hash != normalized_hash:
            raise ObjectIntegrityError(f"stored object hash mismatch: expected {normalized_hash}, got {actual_hash}")
        return body

    def exists(self, content_hash: str) -> bool:
        normalized_hash = validate_content_hash(content_hash)
        key = self._key_for_hash(normalized_hash)
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception:
            return False
        return True

    def _key_for_hash(self, content_hash: str) -> str:
        normalized_hash = validate_content_hash(content_hash)
        path = f"{normalized_hash[:2]}/{normalized_hash[2:4]}/{normalized_hash}"
        if not self.prefix:
            return path
        return f"{self.prefix}/{path}"

    def _create_bucket_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"Bucket": self.bucket}
        if self.region_name and self.region_name != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": self.region_name}
        return kwargs


def _client_error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return ""
    error = response.get("Error")
    if not isinstance(error, dict):
        return ""
    return str(error.get("Code") or "")


def _is_missing_bucket_error(exc: Exception) -> bool:
    return _client_error_code(exc) in {"404", "NoSuchBucket", "NotFound"}


def _is_bucket_already_owned_error(exc: Exception) -> bool:
    return _client_error_code(exc) == "BucketAlreadyOwnedByYou"


__all__ = [
    "ContentAddressableObjectStore",
    "FilesystemObjectStore",
    "ObjectIntegrityError",
    "ObjectNotFoundError",
    "ObjectStorageError",
    "S3CompatibleObjectStore",
    "StoredObject",
    "sha256_bytes",
    "sha256_file",
    "validate_content_hash",
]
