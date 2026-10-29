from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    """Base metadata for future Margin SQLAlchemy models."""


JsonDict = JSONB().with_variant(JSON(), "sqlite")
JsonList = JSONB().with_variant(JSON(), "sqlite")
PgVector = Vector(384).with_variant(JSON(), "sqlite")


def utc_now() -> datetime:
    return datetime.now(UTC)
