from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from margin_db.object_storage import FilesystemObjectStore, S3CompatibleObjectStore
from margin_db.object_store_factory import (
    DEFAULT_OBJECT_STORE_BUCKET,
    DEFAULT_OBJECT_STORE_ENDPOINT,
    DEFAULT_OBJECT_STORE_LOCAL_PATH,
    DEFAULT_OBJECT_STORE_REGION,
    ObjectStoreConfig,
    create_object_store,
    object_store_config_from_env,
)


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


def test_object_store_config_from_env_defaults_to_filesystem(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_object_store_env(monkeypatch)
    monkeypatch.chdir(tmp_path)

    config = object_store_config_from_env()
    store = create_object_store(config)

    assert config.endpoint == DEFAULT_OBJECT_STORE_ENDPOINT
    assert config.bucket == DEFAULT_OBJECT_STORE_BUCKET
    assert config.region == DEFAULT_OBJECT_STORE_REGION
    assert config.filesystem_root == Path(DEFAULT_OBJECT_STORE_LOCAL_PATH)
    assert isinstance(store, FilesystemObjectStore)


def test_create_object_store_uses_filesystem_when_endpoint_missing(tmp_path: Path) -> None:
    store = create_object_store(
        ObjectStoreConfig(
            endpoint="",
            bucket="margin-artifacts",
            region="us-east-1",
            access_key_id=None,
            secret_access_key=None,
            session_token=None,
            filesystem_root=tmp_path / "objects",
        )
    )
    assert isinstance(store, FilesystemObjectStore)


def test_create_object_store_uses_s3_compatible_when_endpoint_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=lambda *args, **kwargs: object()))
    store = create_object_store(
        ObjectStoreConfig(
            endpoint="http://minio:9000",
            bucket="margin-artifacts",
            region="us-east-1",
            access_key_id="margin",
            secret_access_key="margin-password",
            session_token=None,
            filesystem_root=tmp_path / "objects",
        )
    )
    assert isinstance(store, S3CompatibleObjectStore)
