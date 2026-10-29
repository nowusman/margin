from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from margin_db.base import Base
from margin_db.entity_catalog import add_alias, create_entity, upsert_entity_relationship
from margin_db.entity_catalog_candidates import select_relevant_candidates
from margin_db.entity_models import EntityAlias
from margin_db.idea_block_resolution import create_idea_block_revision
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement
from margin_db.user_models import Domain, User


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: object, _record: object) -> None:
        connection.execute("PRAGMA foreign_keys=ON")  # type: ignore[union-attr]

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            (
                Domain(id="commerce", name="Commerce"),
                Domain(id="billing", name="Billing"),
                User(id="reviewer", display_name="Reviewer"),
            )
        )
        session.commit()
        yield session


def test_entity_catalog_rejects_scope_collisions_and_requires_definition_evidence(session: Session) -> None:
    order_api = create_entity(
        session, preferred_label="Order API", ontology_type="api", domain_id="commerce", external_id="service:order-api"
    )

    with pytest.raises(ValueError, match="external_id"):
        create_entity(
            session,
            preferred_label="Replacement API",
            ontology_type="api",
            domain_id="commerce",
            external_id="service:order-api",
        )
    with pytest.raises(ValueError, match="definition IdeaBlock"):
        create_entity(
            session,
            preferred_label="Order API definition",
            ontology_type="concept",
            domain_id="commerce",
            definition="Accepts customer orders.",
        )
    with pytest.raises(ValueError, match="canonical entity label"):
        create_entity(session, preferred_label="Order API", ontology_type="api", domain_id="commerce")
    with pytest.raises(ValueError, match="owner_user_id"):
        create_entity(
            session,
            preferred_label="Unowned API",
            ontology_type="api",
            domain_id="commerce",
            owner_user_id="missing-user",
        )

    payment_api = create_entity(session, preferred_label="Payment API", ontology_type="api", domain_id="commerce")
    with pytest.raises(ValueError, match="already assigned"):
        add_alias(
            session,
            entity=payment_api,
            alias=order_api.preferred_label,
            source_provenance={"kind": "test"},
            actor_id="reviewer",
            reason="test collision",
        )


def test_source_backed_definition_uses_canonical_entities(session: Session) -> None:
    category = create_entity(session, preferred_label="Ontology Source", ontology_type="category", domain_id="commerce")
    source = SourceDocument(
        id="src_ontology",
        title="Reference ontology",
        object_key="ontology.md",
        media_type="text/markdown",
        sha256="ontology-sha",
        domain_id="commerce",
        category_id=category.id,
        metadata_={"ontology_source": True},
    )
    version = SourceDocumentVersion(
        id="src_ontology_v1", source_document_id=source.id, version_sha256="ontology-v1", status="active"
    )
    element = SourceElement(
        id="elem_ontology",
        source_version_id=version.id,
        kind="paragraph",
        ordinal=1,
        text="Order API accepts customer orders.",
    )
    block = IdeaBlock(id="idea_definition", domain_id="commerce")
    session.add_all((source, version, element, block))
    session.flush()
    revision = create_idea_block_revision(
        session,
        block,
        statement="Order API accepts customer orders.",
        normalized_statement="order api accepts customer orders",
        type="fact",
        confidence="verified",
        polarity="supports",
        qualifiers={},
        valid_from=None,
        valid_to=None,
        publication_status="verified",
        actor="reviewer",
        process="manual",
    )
    session.add(
        ClaimEvidence(
            id="evidence_definition",
            idea_block_id=block.id,
            idea_block_revision_id=revision.id,
            source_element_id=element.id,
            source_quote="Order API accepts customer orders.",
            confidence="verified",
            extraction_method="manual",
        )
    )
    session.flush()

    defined = create_entity(
        session,
        preferred_label="Order API",
        ontology_type="api",
        domain_id="commerce",
        definition="Accepts customer orders.",
        definition_idea_block_id=block.id,
    )
    assert defined.definition_idea_block_id == block.id


def test_catalog_rejects_self_relationships(session: Session) -> None:
    api = create_entity(session, preferred_label="Order API", ontology_type="api", domain_id="commerce")

    with pytest.raises(ValueError, match="distinct endpoints"):
        upsert_entity_relationship(session, source=api, target=api, relation_type="depends_on")


def test_catalog_rejects_cross_domain_or_inactive_relationships(session: Session) -> None:
    source = create_entity(session, preferred_label="Commerce API", ontology_type="api", domain_id="commerce")
    target = create_entity(session, preferred_label="Billing API", ontology_type="api", domain_id="billing")

    with pytest.raises(ValueError, match="same domain"):
        upsert_entity_relationship(session, source=source, target=target, relation_type="depends_on")

    target.domain_id = "commerce"
    target.lifecycle_status = "retired"
    with pytest.raises(ValueError, match="active entity endpoints"):
        upsert_entity_relationship(session, source=source, target=target, relation_type="depends_on")


def test_alias_foreign_key_cannot_be_orphaned(session: Session) -> None:
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(
                EntityAlias(
                    id="alias_orphan",
                    entity_id="missing_entity",
                    alias="Orphan",
                    normalized_alias="orphan",
                    locale="en",
                    ontology_type="concept",
                    lifecycle_status="active",
                    source_provenance={},
                )
            )
            session.flush()


def test_candidate_selection_is_bounded_domain_scoped_and_type_filtered(session: Session) -> None:
    api = create_entity(session, preferred_label="Order API", ontology_type="api", domain_id="commerce")
    create_entity(session, preferred_label="Order Service", ontology_type="service", domain_id="commerce")
    billing_api = create_entity(session, preferred_label="Order API", ontology_type="api", domain_id="billing")

    candidates = select_relevant_candidates(
        session,
        domain_id="commerce",
        source_text="The Order API accepts orders.",
        expected_ontology_types=("api",),
        limit=24,
    )

    assert [candidate.canonical_id for candidate in candidates] == [api.id]
    assert billing_api.id not in {candidate.canonical_id for candidate in candidates}
    assert select_relevant_candidates(session, domain_id="commerce", source_text="Order API", limit=0) == ()
