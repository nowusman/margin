from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.cli as cli
from margin_db.base import Base
from margin_db.entity_models import Entity
from margin_db.seed import (
    DEFAULT_DOMAIN_SEEDS,
    default_category_vocabulary,
    default_idea_block_type_vocabulary,
    seed_domains_and_vocabulary,
)
from margin_db.user_models import Domain, Group


def test_seed_domains_creates_default_domains_and_vocabulary() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        result = seed_domains_and_vocabulary(session)
        expected_domains = {domain.code for domain in DEFAULT_DOMAIN_SEEDS}
        expected_type_terms = default_idea_block_type_vocabulary().terms
        expected_category_terms = default_category_vocabulary().terms

        assert result.domains_created == len(DEFAULT_DOMAIN_SEEDS)
        assert result.vocabulary_created == len(DEFAULT_DOMAIN_SEEDS) + len(expected_type_terms) + len(
            expected_category_terms
        )
        assert result.groups_created == 3
        domain_records = session.scalars(select(Domain)).all()
        assert expected_domains == {record.id for record in domain_records}
        assert all(record.owner for record in domain_records)
        assert all(record.steward for record in domain_records)
        terms = {record.preferred_label: record for record in session.scalars(select(Entity)).all()}
        assert terms["BSS"].ontology_type == "domain"
        assert terms["reference_architecture"].id != terms["capability"].id
        group_names = {record.name for record in session.scalars(select(Group)).all()}
        assert group_names == {"margin-admin", "architecture-admins", "architecture-reviewers"}


def test_seed_domains_updates_existing_records_without_duplicates() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Domain(id="BSS", name="Outdated"))
        session.flush()
        result = seed_domains_and_vocabulary(session, domains=(DEFAULT_DOMAIN_SEEDS[1],), commit=False)

        assert result.domains_created == 0
        assert result.domains_updated == 1
        records = session.scalars(select(Domain)).all()
        assert len(records) == 1
        assert records[0].name == "BSS"
        assert records[0].steward == "bss-architecture"


def test_seed_cli_invokes_domain_seed(monkeypatch, capsys) -> None:
    calls: dict[str, object] = {}

    class ContextSession:
        def __enter__(self) -> str:
            return "session"

        def __exit__(self, *args: object) -> None:
            return None

    def fake_sessionmaker(*, bind: object) -> object:
        calls["bind"] = bind
        return ContextSession

    def fake_seed(session: object, *, commit: bool) -> SimpleNamespace:
        calls["session"] = session
        calls["commit"] = commit
        return SimpleNamespace(
            domains_created=5,
            domains_updated=0,
            vocabulary_created=5,
            vocabulary_updated=0,
            groups_created=3,
            groups_updated=0,
            models_created=2,
        )

    monkeypatch.setattr(cli, "create_engine", lambda url: f"engine:{url}")
    monkeypatch.setattr(cli, "sessionmaker", fake_sessionmaker)
    monkeypatch.setattr(cli, "seed_domains_and_vocabulary", fake_seed)

    exit_code = cli.main(["seed-domains", "--database-url", "sqlite://", "--no-commit"])

    assert exit_code == 0
    assert calls == {"bind": "engine:sqlite://", "session": "session", "commit": False}
    assert "created=5" in capsys.readouterr().out
