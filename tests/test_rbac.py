from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from margin_auth.permissions import read_permissions
from margin_db.base import Base
from margin_db.user_models import CategoryPermission, Domain, DomainPermission


def test_read_permissions_combines_user_and_group_permissions() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    with session_factory() as session:
        session.add(Domain(id="OSS", name="OSS", owner="ea", steward="oss"))
        session.add(DomainPermission(domain_id="OSS", principal_type="user", principal_id="alice", permission="read"))
        session.add(
            CategoryPermission(category="ADR", principal_type="group", principal_id="architects", permission="write")
        )
        session.commit()

        snapshot = read_permissions(session, principal_id="alice", group_ids=("architects",))

    assert snapshot.allows_domain("OSS", "read")
    assert snapshot.allows_category("ADR", "write")
    assert snapshot.authz_backend == "postgres_permission_cache"
