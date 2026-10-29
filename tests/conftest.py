from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from margin_db.base import Base
from tests.helpers.source_scope import seed_source_scope


@pytest.fixture()
def session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    seed_source_scope(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
