from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.relationship_routes import router
from margin_auth.permissions import PermissionSnapshot
from margin_db.base import Base
from margin_db.entity_models import Entity
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockEntity, IdeaBlockRevision
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement, SourcePage
from margin_db.user_models import Domain


def test_relationship_api_scopes_neighbors_and_rejects_non_postgres_traversal() -> None:
    client = _client()

    neighbors = client.get("/api/relationships/neighbors/entity-a")
    assert neighbors.status_code == 200
    assert {edge["relationship_id"] for edge in neighbors.json()["edges"]} == {"relationship-a-b", "relationship-a-c"}
    assert all(edge["provenance"] for edge in neighbors.json()["edges"])

    capped = client.get("/api/relationships/neighbors/entity-a?limit=1")
    assert capped.status_code == 200
    assert capped.json()["capped"] is True

    invalid_limit = client.get("/api/relationships/neighbors/entity-a?limit=101")
    assert invalid_limit.status_code == 422

    invalid_governed_limit = client.get("/api/relationships/governed-entity/entity-a?limit=101")
    assert invalid_governed_limit.status_code == 422

    denied = client.get("/api/relationships/neighbors/entity-hidden")
    assert denied.status_code == 403

    path = client.post("/api/relationships/path", json={"source_entity_id": "entity-a", "target_entity_id": "entity-b"})
    assert path.status_code == 503
    assert "PostgreSQL" in path.json()["detail"]


def _client() -> TestClient:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        session.add_all((Domain(id="domain-a", name="Domain A"), Domain(id="domain-hidden", name="Hidden")))
        session.add_all(
            (
                _entity("entity-a", "A"),
                _entity("entity-b", "B"),
                _entity("entity-c", "C"),
                _entity("entity-hidden", "Hidden", domain_id="domain-hidden"),
                Entity(
                    id="category-design",
                    preferred_label="Design Document",
                    normalized_label="design document",
                    ontology_type="category",
                    domain_id=None,
                    lifecycle_status="active",
                ),
                EntityRelationship(
                    id="relationship-a-b",
                    source_entity_id="entity-a",
                    target_entity_id="entity-b",
                    relation_type="depends_on",
                    confidence="verified",
                    status="verified",
                ),
                EntityRelationship(
                    id="relationship-a-hidden",
                    source_entity_id="entity-a",
                    target_entity_id="entity-hidden",
                    relation_type="depends_on",
                    confidence="verified",
                    status="verified",
                ),
                EntityRelationship(
                    id="relationship-a-c",
                    source_entity_id="entity-a",
                    target_entity_id="entity-c",
                    relation_type="depends_on",
                    confidence="verified",
                    status="verified",
                ),
                SourceDocument(
                    id="source-1",
                    title="Architecture source",
                    object_key="source-1.md",
                    media_type="text/markdown",
                    sha256="a" * 64,
                    size_bytes=100,
                    domain_id="domain-a",
                    category_id="category-design",
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
                    text="A depends on B and C.",
                ),
                IdeaBlock(id="idea-block-1", domain_id="domain-a"),
                IdeaBlockRevision(
                    id="idea-block-revision-1",
                    idea_block_id="idea-block-1",
                    revision_number=1,
                    statement="A depends on B and C.",
                    normalized_statement="a depends on b and c",
                    type="claim",
                    confidence="verified",
                    publication_status="verified",
                    semantic_key="a-depends-on-b-and-c",
                ),
                IdeaBlockEntity(idea_block_id="idea-block-1", entity_id="entity-a", role="mentions"),
                ClaimEvidence(
                    id="claim-evidence-1",
                    idea_block_id="idea-block-1",
                    idea_block_revision_id="idea-block-revision-1",
                    source_element_id="source-element-1",
                    source_quote="A depends on B and C.",
                    confidence="verified",
                    extraction_method="test",
                ),
                RelationshipProvenance(
                    id="provenance-a-b",
                    relationship_id="relationship-a-b",
                    idea_block_id="idea-block-1",
                    source_element_id="source-element-1",
                    confidence="verified",
                    extraction_method="test",
                    source_quote="A depends on B and C.",
                    status="verified",
                ),
                RelationshipProvenance(
                    id="provenance-a-c",
                    relationship_id="relationship-a-c",
                    idea_block_id="idea-block-1",
                    source_element_id="source-element-1",
                    confidence="verified",
                    extraction_method="test",
                    source_quote="A depends on B and C.",
                    status="verified",
                ),
            )
        )
        session.flush()
        block = session.get(IdeaBlock, "idea-block-1")
        assert block is not None
        block.current_revision_id = "idea-block-revision-1"
        session.commit()

    app = FastAPI()

    @app.middleware("http")
    async def identity(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.permissions = PermissionSnapshot(
            principal_id="reader",
            domain_permissions={"domain-a": frozenset(("read",))},
            category_permissions={"Design Document": frozenset(("read",))},
        )
        return await call_next(request)

    app.state.session_factory = session_factory
    app.include_router(router)
    return TestClient(app)


def _entity(identifier: str, label: str, *, domain_id: str = "domain-a") -> Entity:
    return Entity(
        id=identifier,
        preferred_label=label,
        normalized_label=label.lower(),
        ontology_type="service",
        domain_id=domain_id,
        lifecycle_status="active",
    )
