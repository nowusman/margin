from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from margin_db.entity_models import Entity
from margin_db.graph_subgraph import authorized_subgraph
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockEntity, IdeaBlockRevision
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement, SourcePage
from margin_schema.ontology import ONTOLOGY_VERSION, validate_relationship_endpoints
from tests.helpers.source_scope import TEST_CATEGORY_ID


def test_ownership_membership_reporting_and_work_assignment_resolve_for_the_widened_kinds() -> None:
    validate_relationship_endpoints("owned_by", "service", "person")
    validate_relationship_endpoints("owned_by", "business_capability", "team")
    validate_relationship_endpoints("has_custodian", "process", "organization")
    validate_relationship_endpoints("member_of", "person", "team")
    validate_relationship_endpoints("member_of", "person", "organization")
    validate_relationship_endpoints("reports_to", "person", "person")
    validate_relationship_endpoints("reports_to", "person", "role")
    validate_relationship_endpoints("works_on", "person", "service")
    validate_relationship_endpoints("works_on", "team", "system")

    # Ownership in the active voice: a platform owns the services it heads.
    validate_relationship_endpoints("owns", "team", "service")
    validate_relationship_endpoints("owns", "person", "api")
    validate_relationship_endpoints("owns", "system", "service")

    # Responsibility is held by an actor, so a record cannot be its owner.
    with pytest.raises(ValueError):
        validate_relationship_endpoints("owned_by", "service", "system")

    with pytest.raises(ValueError):
        validate_relationship_endpoints("member_of", "service", "team")
    with pytest.raises(ValueError):
        validate_relationship_endpoints("reports_to", "service", "person")


def test_the_graph_reports_the_ontology_version_on_the_records_it_returns(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add_all(
            (
                _entity("entity-ada", "Ada Lovelace", "person"),
                _entity("entity-payments", "Payments Team", "team"),
                _entity("entity-api", "Order API", "api"),
            )
        )
        session.add_all(
            (
                _relationship("relationship-ada-payments", "entity-ada", "entity-payments", "member_of"),
                _relationship("relationship-ada-api", "entity-ada", "entity-api", "works_on"),
            )
        )
        session.add_all(
            (
                SourceDocument(
                    id="source-1",
                    title="Organisation chart",
                    object_key="source-1.md",
                    media_type="text/markdown",
                    sha256="a" * 64,
                    size_bytes=100,
                    domain_id="commerce",
                    category_id=TEST_CATEGORY_ID,
                    sensitivity="internal",
                    status="knowledge_ready",
                ),
                SourceDocumentVersion(
                    id="source-version-1", source_document_id="source-1", version_sha256="b" * 64, status="active"
                ),
                SourcePage(
                    id="source-page-1",
                    source_version_id="source-version-1",
                    page_number=1,
                    content_checksum="c" * 64,
                    classification="text_extracted",
                ),
                SourceElement(
                    id="source-element-1",
                    source_version_id="source-version-1",
                    source_page_id="source-page-1",
                    native_element_id="paragraph-1",
                    kind="paragraph",
                    ordinal=0,
                    text="Ada Lovelace is a member of the Payments Team and works on the Order API.",
                ),
                IdeaBlock(id="idea-block-1", domain_id="commerce"),
                IdeaBlockRevision(
                    id="idea-block-revision-1",
                    idea_block_id="idea-block-1",
                    revision_number=1,
                    statement="Ada Lovelace is a member of the Payments Team.",
                    normalized_statement="ada lovelace is a member of the payments team",
                    type="claim",
                    confidence="verified",
                    publication_status="verified",
                    semantic_key="ada-lovelace-is-a-member-of-the-payments-team",
                ),
                IdeaBlockEntity(idea_block_id="idea-block-1", entity_id="entity-ada", role="mentions"),
                ClaimEvidence(
                    id="claim-evidence-1",
                    idea_block_id="idea-block-1",
                    idea_block_revision_id="idea-block-revision-1",
                    source_element_id="source-element-1",
                    source_quote="Ada Lovelace is a member of the Payments Team and works on the Order API.",
                    confidence="verified",
                    extraction_method="test",
                    status="active",
                ),
            )
        )
        for relationship_id, _source_id, _target_id, _relation_type in (
            ("relationship-ada-payments", "entity-ada", "entity-payments", "member_of"),
            ("relationship-ada-api", "entity-ada", "entity-api", "works_on"),
        ):
            session.add(
                RelationshipProvenance(
                    id=f"provenance-{relationship_id}",
                    relationship_id=relationship_id,
                    idea_block_id="idea-block-1",
                    source_element_id="source-element-1",
                    confidence="verified",
                    extraction_method="test",
                    source_quote="Ada Lovelace is a member of the Payments Team and works on the Order API.",
                    status="verified",
                )
            )
        session.commit()
        block = session.get(IdeaBlock, "idea-block-1")
        assert block is not None
        block.current_revision_id = "idea-block-revision-1"
        session.commit()

        subgraph = authorized_subgraph(
            session,
            allowed_entity_ids=frozenset({"entity-ada", "entity-payments", "entity-api"}),
            allowed_idea_block_ids=frozenset({"idea-block-1"}),
            allowed_source_element_ids=frozenset({"source-element-1"}),
            statuses=("verified",),
            depth=1,
        )

        assert {node.id for node in subgraph.nodes} == {"entity-ada", "entity-payments", "entity-api"}
        assert {edge.relation_type for edge in subgraph.edges} == {"member_of", "works_on"}
        assert all(node.ontology_version == ONTOLOGY_VERSION for node in subgraph.nodes)


def _entity(identifier: str, label: str, kind: str) -> Entity:
    return Entity(
        id=identifier,
        preferred_label=label,
        normalized_label=label.lower(),
        ontology_type=kind,
        domain_id="commerce",
        lifecycle_status="active",
        ontology_version=ONTOLOGY_VERSION,
    )


def _relationship(identifier: str, source_id: str, target_id: str, relation_type: str) -> EntityRelationship:
    return EntityRelationship(
        id=identifier,
        source_entity_id=source_id,
        target_entity_id=target_id,
        relation_type=relation_type,
        confidence="verified",
        status="verified",
        ontology_version=ONTOLOGY_VERSION,
    )
