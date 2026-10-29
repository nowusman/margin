from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from margin_db.object_storage import ContentAddressableObjectStore, FilesystemObjectStore, S3CompatibleObjectStore

DEFAULT_OBJECT_STORE_ENDPOINT = ""
DEFAULT_OBJECT_STORE_BUCKET = "margin-artifacts"
DEFAULT_OBJECT_STORE_REGION = "us-east-1"
DEFAULT_OBJECT_STORE_LOCAL_PATH = ".margin/object-store"


@dataclass(frozen=True)
class ObjectStoreConfig:
    endpoint: str
    bucket: str
    region: str
    access_key_id: str | None
    secret_access_key: str | None
    session_token: str | None
    filesystem_root: Path

    @property
    def use_s3_compatible(self) -> bool:
        return bool(self.endpoint.strip())


def object_store_config_from_env() -> ObjectStoreConfig:
    endpoint = os.getenv("MARGIN_OBJECT_STORE_ENDPOINT", DEFAULT_OBJECT_STORE_ENDPOINT).strip()
    bucket = os.getenv("MARGIN_OBJECT_STORE_BUCKET", DEFAULT_OBJECT_STORE_BUCKET).strip()
    region = os.getenv("MARGIN_OBJECT_STORE_REGION", DEFAULT_OBJECT_STORE_REGION).strip()
    filesystem_root = Path(os.getenv("MARGIN_OBJECT_STORE_LOCAL_PATH", DEFAULT_OBJECT_STORE_LOCAL_PATH))

    # Prefer explicit Margin-prefixed credentials. Fall back to AWS standard env names.
    access_key_id = os.getenv("MARGIN_OBJECT_STORE_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY_ID") or None
    secret_access_key = os.getenv("MARGIN_OBJECT_STORE_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY") or None
    session_token = os.getenv("MARGIN_OBJECT_STORE_SESSION_TOKEN") or os.getenv("AWS_SESSION_TOKEN") or None

    return ObjectStoreConfig(
        endpoint=endpoint,
        bucket=bucket or DEFAULT_OBJECT_STORE_BUCKET,
        region=region or DEFAULT_OBJECT_STORE_REGION,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        session_token=session_token,
        filesystem_root=filesystem_root,
    )


def create_object_store(config: ObjectStoreConfig | None = None) -> ContentAddressableObjectStore:
    resolved = config or object_store_config_from_env()
    if resolved.use_s3_compatible:
        return S3CompatibleObjectStore(
            bucket=resolved.bucket,
            endpoint_url=resolved.endpoint,
            region_name=resolved.region,
            aws_access_key_id=resolved.access_key_id,
            aws_secret_access_key=resolved.secret_access_key,
            aws_session_token=resolved.session_token,
        )
    return FilesystemObjectStore(resolved.filesystem_root)


__all__ = [
    "DEFAULT_OBJECT_STORE_BUCKET",
    "DEFAULT_OBJECT_STORE_ENDPOINT",
    "DEFAULT_OBJECT_STORE_LOCAL_PATH",
    "DEFAULT_OBJECT_STORE_REGION",
    "ObjectStoreConfig",
    "create_object_store",
    "object_store_config_from_env",
]
