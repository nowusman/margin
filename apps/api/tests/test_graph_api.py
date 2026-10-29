from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.graph_routes import router
from margin_auth.permissions import PermissionSnapshot
from margin_db.base import Base
from margin_db.entity_models import Entity
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockEntity, IdeaBlockRevision
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement, SourcePage
from margin_db.user_models import Domain


def test_graph_api_returns_only_readable_records_with_evidenced_edges() -> None:
    client = _client()

    subgraph = client.get("/api/graph/subgraph")
    assert subgraph.status_code == 200
    payload = subgraph.json()

    node_ids = {node["id"] for node in payload["nodes"]}
    # The readable category record is in scope even though nothing points at
    # it, because a stranded record is exactly what the graph has to show.
    assert node_ids == {"entity-a", "entity-b", "entity-c", "category-design"}
    assert "entity-hidden" not in node_ids

    edge_ids = {edge["id"] for edge in payload["edges"]}
    assert edge_ids == {"relationship-a-b", "relationship-b-c"}
    assert all(edge["quotes"] for edge in payload["edges"])
    assert all(edge["idea_block_ids"] for edge in payload["edges"])

    assert payload["ontology_version"]
    assert all(node["ontology_version"] == payload["ontology_version"] for node in payload["nodes"])
    assert payload["capped"] is False
    assert payload["communities"]
    assert {node["community_id"] for node in payload["nodes"]}


def test_a_record_outside_the_scope_appears_in_no_part_of_the_graph() -> None:
    client = _client()

    payload = client.get("/api/graph/subgraph").json()
    insights = client.get("/api/graph/insights").json()
    communities = client.get("/api/graph/communities").json()

    assert "entity-hidden" not in {node["id"] for node in payload["nodes"]}
    assert "entity-hidden" not in {edge["id"] for edge in payload["edges"]}
    assert all("entity-hidden" not in community["member_ids"] for community in payload["communities"])
    assert all("entity-hidden" not in community["member_ids"] for community in communities["communities"])
    assert all("entity-hidden" not in gap["node_ids"] for gap in insights["knowledge_gaps"])
    assert all(
        "entity-hidden" not in record["id"] for gap in insights["knowledge_gaps"] for record in gap["affected_records"]
    )
    assert all(
        connection["source_id"] != "entity-hidden" and connection["target_id"] != "entity-hidden"
        for connection in insights["surprising_connections"]
    )


def test_graph_api_reports_communities_and_knowledge_gaps() -> None:
    client = _client()

    communities = client.get("/api/graph/communities")
    assert communities.status_code == 200
    assert communities.json()["communities"]
    assert communities.json()["node_count"] == 4

    insights = client.get("/api/graph/insights")
    assert insights.status_code == 200
    insight_payload = insights.json()
    assert "surprising_connections" in insight_payload
    assert "knowledge_gaps" in insight_payload


def test_graph_api_dismissal_removes_a_connection_from_later_reads() -> None:
    client = _client()

    dismissal = client.post(
        "/api/graph/insights/decisions", json={"target_id": "relationship-a-b", "reason": "not useful"}
    )
    assert dismissal.status_code == 200
    assert dismissal.json() == {
        "status": "dismiss",
        "target_id": "relationship-a-b",
        "target_type": "connection",
        "deferred_until": None,
    }

    repeated = client.post(
        "/api/graph/insights/decisions", json={"target_id": "relationship-a-b", "reason": "still not useful"}
    )
    assert repeated.status_code == 200

    dismissed = client.get("/api/graph/insights")
    assert dismissed.json()["hidden_count"] == 1


def test_graph_api_deferral_hides_a_gap_until_it_expires() -> None:
    client = _client()

    gaps = client.get("/api/graph/insights").json()["knowledge_gaps"]
    assert gaps
    gap_key = gaps[0]["gap_key"]
    assert gaps[0]["affected_records"]

    deferred = client.post(
        "/api/graph/insights/decisions",
        json={"target_id": gap_key, "target_type": "gap", "decision": "defer", "defer_days": 7},
    )
    assert deferred.status_code == 200
    assert deferred.json()["status"] == "defer"
    assert deferred.json()["deferred_until"]

    after = client.get("/api/graph/insights").json()
    assert gap_key not in {gap["gap_key"] for gap in after["knowledge_gaps"]}
    assert after["hidden_count"] == 1


def test_graph_api_rejects_an_unknown_decision_kind() -> None:
    client = _client()

    unknown_decision = client.post(
        "/api/graph/insights/decisions", json={"target_id": "relationship-a-b", "decision": "ignore"}
    )
    assert unknown_decision.status_code == 422

    unknown_target = client.post(
        "/api/graph/insights/decisions", json={"target_id": "relationship-a-b", "target_type": "weather"}
    )
    assert unknown_target.status_code == 422


def test_graph_api_rejects_a_root_outside_the_readable_scope() -> None:
    client = _client()

    denied = client.get("/api/graph/subgraph?root_entity_id=entity-hidden")
    assert denied.status_code == 422

    unknown_type = client.get("/api/graph/subgraph?relation_types=not_a_relation")
    assert unknown_type.status_code == 422


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
                _relationship("relationship-a-b", "entity-a", "entity-b"),
                _relationship("relationship-b-c", "entity-b", "entity-c"),
                _relationship("relationship-a-hidden", "entity-a", "entity-hidden"),
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
                    text="A depends on B, and B depends on C.",
                ),
                IdeaBlock(id="idea-block-1", domain_id="domain-a"),
                IdeaBlockRevision(
                    id="idea-block-revision-1",
                    idea_block_id="idea-block-1",
                    revision_number=1,
                    statement="A depends on B, and B depends on C.",
                    normalized_statement="a depends on b and b depends on c",
                    type="claim",
                    confidence="verified",
                    publication_status="verified",
                    semantic_key="a-depends-on-b-and-b-depends-on-c",
                ),
                IdeaBlockEntity(idea_block_id="idea-block-1", entity_id="entity-a", role="mentions"),
                ClaimEvidence(
                    id="claim-evidence-1",
                    idea_block_id="idea-block-1",
                    idea_block_revision_id="idea-block-revision-1",
                    source_element_id="source-element-1",
                    source_quote="A depends on B, and B depends on C.",
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
                    source_quote="A depends on B, and B depends on C.",
                    status="verified",
                ),
                RelationshipProvenance(
                    id="provenance-b-c",
                    relationship_id="relationship-b-c",
                    idea_block_id="idea-block-1",
                    source_element_id="source-element-1",
                    confidence="verified",
                    extraction_method="test",
                    source_quote="A depends on B, and B depends on C.",
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
        request.state.principal_id = "reader"
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


def _relationship(identifier: str, source: str, target: str) -> EntityRelationship:
    return EntityRelationship(
        id=identifier,
        source_entity_id=source,
        target_entity_id=target,
        relation_type="depends_on",
        confidence="verified",
        status="verified",
    )
