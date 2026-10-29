from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from margin_db.base import Base
from margin_db.entity_models import Entity
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockRevision
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.relationship_queries import (
    RelationshipDatabaseConfigurationError,
    bounded_path,
    direct_neighbors,
    relationship_provenance_scope,
    with_authorized_provenance,
)
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement
from margin_db.user_models import Domain


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Domain(id="domain-a", name="Domain A"))
        session.add_all(
            (
                Entity(
                    id="category-design",
                    preferred_label="Design",
                    normalized_label="design",
                    ontology_type="category",
                    domain_id="domain-a",
                    lifecycle_status="active",
                ),
                _entity("entity-a", "A"),
                _entity("entity-b", "B"),
                _entity("entity-c", "C"),
            )
        )
        session.add(
            SourceDocument(
                id="source-1",
                title="Architecture source",
                object_key="source-1",
                media_type="text/plain",
                sha256="a" * 64,
                domain_id="domain-a",
                category_id="category-design",
                sensitivity="internal",
            )
        )
        session.add(
            SourceDocumentVersion(id="source-version-1", source_document_id="source-1", version_sha256="b" * 64)
        )
        session.add(
            SourceElement(
                id="source-element-1",
                source_version_id="source-version-1",
                kind="paragraph",
                ordinal=1,
                text="A depends on B.",
            )
        )
        block = IdeaBlock(id="idea-block-1", domain_id="domain-a")
        revision = IdeaBlockRevision(
            id="idea-block-revision-1",
            idea_block_id=block.id,
            revision_number=1,
            statement="A depends on B.",
            normalized_statement="a depends on b",
            type="claim",
            confidence="verified",
            publication_status="verified",
            semantic_key="a-depends-on-b",
        )
        session.add_all((block, revision))
        session.flush()
        block.current_revision_id = revision.id
        session.add(
            ClaimEvidence(
                id="claim-evidence-1",
                idea_block_id=block.id,
                idea_block_revision_id=revision.id,
                source_element_id="source-element-1",
                source_quote="The source quote is exact.",
                confidence="verified",
                extraction_method="test",
            )
        )
        session.add_all(
            (
                _relationship("relationship-a-b", "entity-a", "entity-b"),
                _relationship("relationship-a-c", "entity-a", "entity-c"),
            )
        )
        session.commit()
        yield session


def _entity(identifier: str, label: str) -> Entity:
    return Entity(
        id=identifier,
        preferred_label=label,
        normalized_label=label.lower(),
        ontology_type="service",
        domain_id="domain-a",
        lifecycle_status="active",
    )


def _relationship(identifier: str, source_id: str, target_id: str) -> EntityRelationship:
    return EntityRelationship(
        id=identifier,
        source_entity_id=source_id,
        target_entity_id=target_id,
        relation_type="depends_on",
        confidence="verified",
        status="verified",
    )


def test_direct_neighbors_filters_unreadable_endpoints_in_the_query(session: Session) -> None:
    result = direct_neighbors(
        session,
        entity_id="entity-a",
        allowed_entity_ids=frozenset(("entity-a", "entity-b")),
        relation_types=("depends_on",),
    )

    assert [edge.relationship_id for edge in result.edges] == ["relationship-a-b"]
    assert result.edges[0].source.id == "entity-a"
    assert result.edges[0].target.id == "entity-b"


def test_direct_neighbors_reports_only_real_truncation(session: Session) -> None:
    exact = direct_neighbors(
        session,
        entity_id="entity-a",
        allowed_entity_ids=frozenset(("entity-a", "entity-b", "entity-c")),
        relation_types=("depends_on",),
        limit=2,
    )
    assert len(exact.edges) == 2
    assert exact.capped is False

    truncated = direct_neighbors(
        session,
        entity_id="entity-a",
        allowed_entity_ids=frozenset(("entity-a", "entity-b", "entity-c")),
        relation_types=("depends_on",),
        limit=1,
    )
    assert len(truncated.edges) == 1
    assert truncated.capped is True


def test_path_does_not_fall_back_to_unbounded_in_memory_traversal(session: Session) -> None:
    with pytest.raises(RelationshipDatabaseConfigurationError, match="PostgreSQL"):
        bounded_path(
            session,
            source_entity_id="entity-a",
            target_entity_id="entity-b",
            allowed_entity_ids=frozenset(("entity-a", "entity-b")),
            max_depth=2,
        )


def test_authorized_provenance_requires_current_verified_evidence(session: Session) -> None:
    session.add_all(
        RelationshipProvenance(
            id=f"provenance-{status}",
            relationship_id="relationship-a-b",
            idea_block_id="idea-block-1",
            source_element_id="source-element-1",
            confidence="verified",
            extraction_method="test",
            source_quote="The source quote is exact.",
            status=status,
        )
        for status in ("candidate", "verified", "rejected", "stale")
    )
    session.commit()

    edges = direct_neighbors(session, entity_id="entity-a", allowed_entity_ids=frozenset(("entity-a", "entity-b")))
    enriched = with_authorized_provenance(
        session,
        edges=edges.edges,
        allowed_idea_block_ids=frozenset(("idea-block-1",)),
        allowed_source_element_ids=frozenset(("source-element-1",)),
    )

    assert {item.status for item in enriched[0].provenance} == {"verified"}


def test_authorized_provenance_rejects_stale_claim_evidence(session: Session) -> None:
    session.add(
        RelationshipProvenance(
            id="provenance-current",
            relationship_id="relationship-a-b",
            idea_block_id="idea-block-1",
            source_element_id="source-element-1",
            confidence="verified",
            extraction_method="test",
            source_quote="The source quote is exact.",
            status="verified",
        )
    )
    session.commit()

    edges = direct_neighbors(session, entity_id="entity-a", allowed_entity_ids=frozenset(("entity-a", "entity-b")))
    claim = session.get(ClaimEvidence, "claim-evidence-1")
    assert claim is not None
    claim.status = "stale"
    session.commit()

    enriched = with_authorized_provenance(
        session,
        edges=edges.edges,
        allowed_idea_block_ids=frozenset(("idea-block-1",)),
        allowed_source_element_ids=frozenset(("source-element-1",)),
    )
    assert enriched == ()


def test_derived_inverse_uses_authorized_original_provenance(session: Session) -> None:
    original = EntityRelationship(
        id="relationship-contains",
        source_entity_id="entity-a",
        target_entity_id="entity-b",
        relation_type="contains",
        confidence="verified",
        status="verified",
    )
    inverse = EntityRelationship(
        id="relationship-part-of",
        source_entity_id="entity-b",
        target_entity_id="entity-a",
        relation_type="part_of",
        confidence="verified",
        status="verified",
        metadata_={"derived": True, "inverse_of": original.id},
    )
    claim = session.get(ClaimEvidence, "claim-evidence-1")
    assert claim is not None
    claim.source_quote = "A contains B."
    session.add_all(
        (
            original,
            inverse,
            RelationshipProvenance(
                id="provenance-contains",
                relationship_id=original.id,
                idea_block_id="idea-block-1",
                source_element_id="source-element-1",
                confidence="verified",
                extraction_method="test",
                source_quote="A contains B.",
                status="verified",
            ),
        )
    )
    session.commit()

    scope, provenance_sources = relationship_provenance_scope(session, (inverse.id,))
    assert scope == (inverse.id, original.id)
    assert provenance_sources == {inverse.id: original.id}

    result = direct_neighbors(
        session,
        entity_id="entity-a",
        allowed_entity_ids=frozenset(("entity-a", "entity-b")),
        relation_types=("part_of",),
    )
    enriched = with_authorized_provenance(
        session,
        edges=result.edges,
        allowed_idea_block_ids=frozenset(("idea-block-1",)),
        allowed_source_element_ids=frozenset(("source-element-1",)),
    )

    assert [edge.relationship_id for edge in enriched] == [inverse.id]
    assert [item.source_quote for item in enriched[0].provenance] == ["A contains B."]
