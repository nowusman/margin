from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

import margin_db.object_store_factory as object_store_factory
from app.config import Settings
from margin_db.object_storage import ContentAddressableObjectStore, FilesystemObjectStore, StoredObject


@dataclass
class RecordingStore(ContentAddressableObjectStore):
    ready_calls: int = 0

    def ensure_ready(self) -> None:
        self.ready_calls += 1

    def put_bytes(self, data: bytes) -> StoredObject:
        raise NotImplementedError

    def get_bytes(self, content_hash: str) -> bytes:
        raise NotImplementedError

    def exists(self, content_hash: str) -> bool:
        raise NotImplementedError


def _clear_object_store_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_name in (
        "MARGIN_OBJECT_STORE_ENDPOINT",
        "MARGIN_OBJECT_STORE_BUCKET",
        "MARGIN_OBJECT_STORE_REGION",
        "MARGIN_OBJECT_STORE_LOCAL_PATH",
        "MARGIN_OBJECT_STORE_ACCESS_KEY_ID",
        "MARGIN_OBJECT_STORE_SECRET_ACCESS_KEY",
        "MARGIN_OBJECT_STORE_SESSION_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    ):
        monkeypatch.delenv(env_name, raising=False)


def _object_store_config_from_settings(settings: Settings) -> object_store_factory.ObjectStoreConfig:
    return object_store_factory.ObjectStoreConfig(
        endpoint=settings.object_store_endpoint,
        bucket=settings.object_store_bucket,
        region=settings.object_store_region,
        access_key_id=settings.object_store_access_key_id,
        secret_access_key=settings.object_store_secret_access_key,
        session_token=settings.object_store_session_token,
        filesystem_root=Path(settings.object_store_local_path),
    )


def test_api_and_worker_defaults_resolve_to_filesystem(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_object_store_env(monkeypatch)
    monkeypatch.chdir(tmp_path)

    api_config = _object_store_config_from_settings(Settings(_env_file=None))
    worker_config = object_store_factory.object_store_config_from_env()

    assert api_config == worker_config
    assert api_config.use_s3_compatible == worker_config.use_s3_compatible
    assert isinstance(object_store_factory.create_object_store(api_config), FilesystemObjectStore)
    worker_store = object_store_factory.create_object_store(worker_config)

    assert isinstance(worker_store, FilesystemObjectStore)


def test_create_object_store_returns_s3_store_for_configured_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    recording = RecordingStore()

    def _factory(**kwargs: Any) -> ContentAddressableObjectStore:
        assert kwargs["bucket"] == "margin-artifacts"
        return recording

    monkeypatch.setattr(object_store_factory, "S3CompatibleObjectStore", _factory)

    store = object_store_factory.create_object_store(
        object_store_factory.ObjectStoreConfig(
            endpoint="http://minio:9000",
            bucket="margin-artifacts",
            region="us-east-1",
            access_key_id="margin",
            secret_access_key="margin-minio-password",
            session_token=None,
            filesystem_root=Path(".margin/object-store"),
        )
    )

    assert store is recording
    assert recording.ready_calls == 0


def test_create_object_store_keeps_filesystem_store_local(tmp_path: Path) -> None:
    store = object_store_factory.create_object_store(
        object_store_factory.ObjectStoreConfig(
            endpoint="",
            bucket="margin-artifacts",
            region="us-east-1",
            access_key_id=None,
            secret_access_key=None,
            session_token=None,
            filesystem_root=tmp_path / "object-store",
        )
    )

    assert isinstance(store, FilesystemObjectStore)
