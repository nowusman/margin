from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from margin_db.entity_catalog import resolve_entity_filter
from margin_db.entity_models import Entity, EntityAlias
from margin_db.governance_models import GovernanceRecord, GovernanceRecordIdeaBlock, GovernanceRecordRevision
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockEntity, IdeaBlockRelation, IdeaBlockRevision
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement
from margin_db.user_models import Domain
from margin_retrieval import candidates as candidate_module
from margin_retrieval.candidates import FusedCandidate, RetrievalCandidate, fuse_candidates, lexical_candidates
from margin_retrieval.expansion import expand_results
from margin_retrieval.hydration import hydrate_results
from margin_retrieval.interfaces import RetrievalExpansion, RetrievalScope
from margin_retrieval.policy import RetrievalPermissionError, validate_expansion


def test_rrf_fuses_unique_revision_ids_with_named_channel_ranks() -> None:
    fused = fuse_candidates(
        (
            (RetrievalCandidate("rev_b", "lexical", 1, 0.9), RetrievalCandidate("rev_a", "lexical", 2, 0.8)),
            (RetrievalCandidate("rev_a", "dense", 1, 0.9), RetrievalCandidate("rev_b", "dense", 2, 0.8)),
        )
    )

    assert [candidate.revision_id for candidate in fused] == ["rev_a", "rev_b"]
    assert fused[0].channel_ranks == {"lexical": 2, "dense": 1}


def test_lexical_candidates_use_persisted_vector_without_outer_hydration_joins(monkeypatch) -> None:
    session = _CaptureSession()
    monkeypatch.setattr(candidate_module, "is_postgresql", lambda _session: True)

    lexical_candidates(
        session,
        query_text="service architecture",
        scope=RetrievalScope(domain_ids=("domain_1",), all_category_ids_authorized=True, sensitivities=("internal",)),
    )

    compiled = str(session.statement.compile(dialect=postgresql.dialect()))
    assert "websearch_to_tsquery" in compiled
    assert "idea_block_revisions.search_vector @@" in compiled
    assert "SELECT idea_block_revisions.id" in compiled
    assert "JOIN claim_evidence" not in compiled.split("WHERE", maxsplit=1)[0]
    assert "idea_block_entities" not in compiled


def test_hydration_keeps_independent_citations_and_redacts_restricted_sources() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    from margin_db.base import Base

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_hydration_data(session)
        results = hydrate_results(
            session,
            candidates=(FusedCandidate("rev_1", 0.2, {"lexical": 1}),),
            scope=RetrievalScope(
                domain_ids=("domain_1",),
                category_ids=("category_1",),
                sensitivities=("internal",),
                governance_record_types=("risk",),
            ),
        )

    assert len(results) == 1
    result = results[0]
    assert result.metadata["final_rank"] == 1
    assert result.metadata["channel_ranks"] == {"lexical": 1}
    assert result.metadata["governance"] == [
        {
            "id": "risk_1",
            "revision_id": "risk_rev_1",
            "type": "risk",
            "status": "identified",
            "title": "Risk one",
            "role": "governs",
        }
    ]
    assert [citation.polarity for citation in result.citations] == ["supports", "refutes", "supports", "supports"]
    assert result.citations[2].governance_record_revision_id == "risk_rev_1"
    assert result.citations[3].redacted is True
    assert result.citations[3].source_document_id is None
    assert result.citations[0].source_version_sha256 == "visible-sha"
    assert result.citations[0].quote == "Visible supporting quote"
    assert result.metadata["type"] == "architecture"
    assert result.metadata["publication_status"] == "verified"
    assert result.metadata["relations"] == [
        {
            "id": "relation_1",
            "source_idea_block_id": "block_1",
            "target_idea_block_id": "block_2",
            "type": "contradicts",
            "status": "verified",
        }
    ]


def test_hydration_deduplicates_revision_candidates() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    from margin_db.base import Base

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_hydration_data(session)
        results = hydrate_results(
            session,
            candidates=(FusedCandidate("rev_1", 0.2, {"lexical": 1}), FusedCandidate("rev_1", 0.1, {"dense": 2})),
            scope=RetrievalScope(domain_ids=("domain_1",), category_ids=("category_1",), sensitivities=("internal",)),
        )

    assert len(results) == 1
    assert results[0].score == 0.2


def test_hydration_rechecks_candidate_authorization_before_returning_results() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    from margin_db.base import Base

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_hydration_data(session)
        results = hydrate_results(
            session,
            candidates=(FusedCandidate("rev_2", 0.2, {"lexical": 1}),),
            scope=RetrievalScope(
                domain_ids=("domain_1",),
                category_ids=("category_1",),
                source_document_ids=("source_restricted",),
                sensitivities=("confidential",),
            ),
        )

    assert results == ()


def test_hydration_uses_only_current_source_versions_and_governance_revisions() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    from margin_db.base import Base

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_hydration_data(session)
        old_source_version = SourceDocumentVersion(
            id="version_old", source_document_id="source_visible", version_sha256="old-sha", status="superseded"
        )
        session.add(old_source_version)
        session.flush()
        session.add(
            SourceElement(
                id="element_old",
                source_version_id=old_source_version.id,
                kind="paragraph",
                ordinal=2,
                text="Old source text",
            )
        )
        old_governance_revision = GovernanceRecordRevision(
            id="risk_rev_old",
            governance_record_id="risk_1",
            revision_number=0,
            snapshot={},
            transition_reason="superseded",
            actor="tester",
            process="test",
        )
        session.add(old_governance_revision)
        session.flush()
        session.add_all(
            [
                ClaimEvidence(
                    id="evidence_old_source",
                    idea_block_id="block_1",
                    idea_block_revision_id="rev_1",
                    source_element_id="element_old",
                    source_quote="Old source quote",
                    confidence="verified",
                    extraction_method="test",
                ),
                ClaimEvidence(
                    id="evidence_old_governance",
                    idea_block_id="block_1",
                    idea_block_revision_id="rev_1",
                    governance_record_revision_id=old_governance_revision.id,
                    source_quote="Old governance quote",
                    confidence="verified",
                    extraction_method="test",
                ),
            ]
        )
        session.commit()

        results = hydrate_results(
            session,
            candidates=(FusedCandidate("rev_1", 0.2, {"lexical": 1}),),
            scope=RetrievalScope(
                domain_ids=("domain_1",),
                category_ids=("category_1",),
                sensitivities=("internal",),
                governance_record_types=("risk",),
            ),
        )

    citation_ids = {citation.citation_id for citation in results[0].citations}
    assert "evidence_old_source" not in citation_ids
    assert "evidence_old_governance" not in citation_ids


def test_hydration_rejects_cross_domain_source_evidence() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    from margin_db.base import Base

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_hydration_data(session)
        session.add(Domain(id="domain_2", name="Domain Two"))
        source = session.get(SourceDocument, "source_visible")
        assert source is not None
        source.domain_id = "domain_2"
        session.commit()

        results = hydrate_results(
            session,
            candidates=(FusedCandidate("rev_1", 0.2, {"lexical": 1}),),
            scope=RetrievalScope(domain_ids=("domain_1",), category_ids=("category_1",), sensitivities=("internal",)),
        )

    assert results == ()


def test_relationship_filter_requires_exact_current_source_evidence() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    from margin_db.base import Base

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_hydration_data(session)
        scope = RetrievalScope(
            domain_ids=("domain_1",),
            category_ids=("category_1",),
            sensitivities=("internal",),
            relationship_types=("depends_on",),
        )
        assert candidate_module.authorized_current_revisions_for_block_ids(
            session, block_ids=("block_1",), scope=scope
        ) == {"block_1": "rev_1"}

        evidence = session.get(ClaimEvidence, "evidence_visible_support")
        assert evidence is not None
        evidence.source_quote = "A different active quote"
        session.commit()

        assert (
            candidate_module.authorized_current_revisions_for_block_ids(session, block_ids=("block_1",), scope=scope)
            == {}
        )


def test_dense_candidates_do_not_call_provider_without_active_assignment(monkeypatch) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    from margin_db.base import Base

    Base.metadata.create_all(engine)
    calls: list[str] = []

    class Provider:
        model_id = "embedding-model"
        index_version = "embedding-index"
        dimensions = 384

        def embed(self, texts: tuple[str, ...]) -> tuple[list[float], ...]:
            calls.extend(texts)
            return ()

    monkeypatch.setattr(candidate_module, "is_postgresql", lambda _session: True)
    with Session(engine) as session:
        result = candidate_module.dense_candidates(
            session, query_text="architecture", scope=RetrievalScope(), embedding_provider=Provider(), deadline_ms=250
        )

    assert result.mode == "assignment_unavailable"
    assert calls == []


def test_expansion_uses_only_verified_explicit_edges_and_rechecks_read_scope() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    from margin_db.base import Base

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_hydration_data(session)
        candidate, envelope = expand_results(
            session,
            seed_block_ids=("block_1",),
            scope=RetrievalScope(domain_ids=("domain_1",), category_ids=("category_1",), sensitivities=("internal",)),
            expansion=RetrievalExpansion(relation_types=("contradicts",), depth=1, limit=20),
        )

    assert envelope.status == "completed"
    assert [item.revision_id for item in candidate] == ["rev_2"]
    assert candidate[0].channel_ranks == {"expansion": 1}


def test_block_expansion_quarantines_missing_decision_provenance() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    from margin_db.base import Base

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_hydration_data(session)
        relation = session.get(IdeaBlockRelation, "relation_1")
        assert relation is not None
        relation.decision_provenance = {}
        session.commit()

        candidates, envelope = expand_results(
            session,
            seed_block_ids=("block_1",),
            scope=RetrievalScope(domain_ids=("domain_1",), category_ids=("category_1",), sensitivities=("internal",)),
            expansion=RetrievalExpansion(relation_types=("contradicts",), depth=1, limit=20),
        )

    assert envelope.status == "completed"
    assert candidates == ()


def test_expansion_can_traverse_verified_entity_relationships() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    from margin_db.base import Base

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_hydration_data(session)
        candidate, envelope = expand_results(
            session,
            seed_block_ids=("block_1",),
            scope=RetrievalScope(domain_ids=("domain_1",), category_ids=("category_1",), sensitivities=("internal",)),
            expansion=RetrievalExpansion(relation_types=("depends_on",), depth=1, limit=20),
            deadline_ms=60_000,
        )

    assert envelope.status == "completed"
    assert [item.revision_id for item in candidate] == ["rev_2"]


@pytest.mark.parametrize(
    "invalidated",
    (
        "missing_provenance",
        "unverified_provenance",
        "mismatched_polarity",
        "cross_domain_source",
        "inactive_source",
        "inactive_claim",
        "inactive_endpoint",
    ),
)
def test_entity_expansion_requires_current_verified_evidence_and_active_endpoints(invalidated: str) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    from margin_db.base import Base

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_hydration_data(session)
        relationship = session.get(EntityRelationship, "entity_relation_1")
        provenance = session.get(RelationshipProvenance, "relationship_provenance_1")
        assert relationship is not None
        assert provenance is not None
        if invalidated == "missing_provenance":
            session.delete(provenance)
        elif invalidated == "unverified_provenance":
            provenance.status = "candidate"
        elif invalidated == "mismatched_polarity":
            provenance.polarity = "refutes"
        elif invalidated == "cross_domain_source":
            domain = session.get(Domain, "domain_2")
            if domain is None:
                session.add(Domain(id="domain_2", name="Domain Two"))
                session.flush()
            source = session.get(SourceDocument, "source_visible")
            assert source is not None
            source.domain_id = "domain_2"
        elif invalidated == "inactive_source":
            source_version = session.get(SourceDocumentVersion, "version_visible")
            assert source_version is not None
            source_version.status = "superseded"
        elif invalidated == "inactive_claim":
            claim = session.scalar(select(ClaimEvidence).where(ClaimEvidence.id == "evidence_visible_support"))
            assert claim is not None
            claim.status = "stale"
        else:
            endpoint = session.get(Entity, "entity_2")
            assert endpoint is not None
            endpoint.lifecycle_status = "retired"
        session.commit()

        candidates, envelope = expand_results(
            session,
            seed_block_ids=("block_1",),
            scope=RetrievalScope(domain_ids=("domain_1",), category_ids=("category_1",), sensitivities=("internal",)),
            expansion=RetrievalExpansion(relation_types=("depends_on",), depth=1, limit=20),
            deadline_ms=60_000,
        )

    assert envelope.status == "completed"
    assert candidates == ()


def test_entity_expansion_rejects_relationship_provenance_outside_source_scope() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    from margin_db.base import Base

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_hydration_data(session)
        provenance = session.get(RelationshipProvenance, "relationship_provenance_1")
        assert provenance is not None
        provenance.source_element_id = "element_restricted"
        provenance.source_quote = "Restricted relationship quote"
        restricted_claim = session.get(ClaimEvidence, "evidence_restricted")
        assert restricted_claim is not None
        restricted_claim.source_quote = "Restricted relationship quote"
        session.commit()

        candidates, envelope = expand_results(
            session,
            seed_block_ids=("block_1",),
            scope=RetrievalScope(domain_ids=("domain_1",), category_ids=("category_1",), sensitivities=("internal",)),
            expansion=RetrievalExpansion(relation_types=("depends_on",), depth=1, limit=20),
            deadline_ms=60_000,
        )

    assert envelope.status == "completed"
    assert candidates == ()


def test_entity_expansion_uses_original_provenance_for_valid_derived_inverse() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    from margin_db.base import Base

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _seed_hydration_data(session)
        session.add_all(
            [
                EntityRelationship(
                    id="entity_relation_contains",
                    source_entity_id="entity_1",
                    target_entity_id="entity_2",
                    relation_type="contains",
                    confidence="verified",
                    status="verified",
                ),
                EntityRelationship(
                    id="entity_relation_part_of",
                    source_entity_id="entity_2",
                    target_entity_id="entity_1",
                    relation_type="part_of",
                    confidence="verified",
                    status="verified",
                    metadata_={"derived": True, "inverse_of": "entity_relation_contains"},
                ),
                RelationshipProvenance(
                    id="relationship_provenance_contains",
                    relationship_id="entity_relation_contains",
                    idea_block_id="block_1",
                    source_element_id="element_visible",
                    source_quote="Visible supporting quote",
                    confidence="verified",
                    extraction_method="test",
                    status="verified",
                ),
            ]
        )
        session.commit()

        candidates, envelope = expand_results(
            session,
            seed_block_ids=("block_1",),
            scope=RetrievalScope(domain_ids=("domain_1",), category_ids=("category_1",), sensitivities=("internal",)),
            expansion=RetrievalExpansion(relation_types=("part_of",), depth=1, limit=20),
        )

    assert envelope.status == "completed"
    assert [item.revision_id for item in candidates] == ["rev_2"]


def test_entity_term_resolution_returns_ambiguity_without_selecting_a_match() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    from margin_db.base import Base

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Entity(
                    id="component_payment",
                    preferred_label="Payments Component",
                    normalized_label="payments component",
                    ontology_type="component",
                    domain_id="domain_1",
                    lifecycle_status="active",
                ),
                Entity(
                    id="service_payment",
                    preferred_label="Payments Service",
                    normalized_label="payments service",
                    ontology_type="service",
                    domain_id="domain_1",
                    lifecycle_status="active",
                ),
            ]
        )
        session.add_all(
            [
                EntityAlias(
                    id="alias_component_payment",
                    entity_id="component_payment",
                    alias="Payments",
                    normalized_alias="payments",
                    ontology_type="component",
                    lifecycle_status="active",
                ),
                EntityAlias(
                    id="alias_service_payment",
                    entity_id="service_payment",
                    alias="Payments",
                    normalized_alias="payments",
                    ontology_type="service",
                    lifecycle_status="active",
                ),
            ]
        )
        session.commit()
        resolution = resolve_entity_filter(session, text="Payments", domain_ids=("domain_1",))

    assert resolution.entity_id is None
    assert resolution.is_ambiguous is True
    assert [candidate["id"] for candidate in resolution.candidates] == ["component_payment", "service_payment"]


def test_expansion_validation_rejects_unbounded_or_unknown_relations() -> None:
    with pytest.raises(RetrievalPermissionError, match="positive depth"):
        validate_expansion(RetrievalExpansion(relation_types=("contains",), limit=1))
    with pytest.raises(RetrievalPermissionError, match="unknown expansion relation type"):
        validate_expansion(RetrievalExpansion(relation_types=("similarity",), depth=1, limit=1))


def _seed_hydration_data(session: Session) -> None:
    session.add_all(
        [
            Domain(id="domain_1", name="Domain One"),
            Entity(
                id="category_1",
                preferred_label="Architecture",
                normalized_label="architecture",
                ontology_type="category",
                lifecycle_status="active",
            ),
            Entity(
                id="entity_1",
                preferred_label="Service A",
                normalized_label="service a",
                ontology_type="component",
                domain_id="domain_1",
                lifecycle_status="active",
            ),
            Entity(
                id="entity_2",
                preferred_label="Service B",
                normalized_label="service b",
                ontology_type="component",
                domain_id="domain_1",
                lifecycle_status="active",
            ),
            SourceDocument(
                id="source_visible",
                title="Visible source",
                object_key="visible",
                media_type="text/plain",
                sha256="visible",
                domain_id="domain_1",
                category_id="category_1",
                sensitivity="internal",
            ),
            SourceDocument(
                id="source_restricted",
                title="Restricted source",
                object_key="restricted",
                media_type="text/plain",
                sha256="restricted",
                domain_id="domain_1",
                category_id="category_1",
                sensitivity="confidential",
            ),
        ]
    )
    session.flush()
    session.add_all(
        [
            SourceDocumentVersion(
                id="version_visible", source_document_id="source_visible", version_sha256="visible-sha", status="active"
            ),
            SourceDocumentVersion(
                id="version_restricted",
                source_document_id="source_restricted",
                version_sha256="restricted-sha",
                status="active",
            ),
        ]
    )
    session.flush()
    session.add_all(
        [
            SourceElement(
                id="element_visible",
                source_version_id="version_visible",
                kind="paragraph",
                ordinal=1,
                text="Visible source text",
                page_number=1,
            ),
            SourceElement(
                id="element_restricted",
                source_version_id="version_restricted",
                kind="paragraph",
                ordinal=1,
                text="Restricted source text",
                page_number=1,
            ),
        ]
    )
    block = IdeaBlock(id="block_1", domain_id="domain_1")
    revision = IdeaBlockRevision(
        id="rev_1",
        idea_block_id="block_1",
        revision_number=1,
        statement="Service A uses a visible architecture pattern.",
        normalized_statement="service a uses a visible architecture pattern",
        search_text="service a uses a visible architecture pattern",
        type="architecture",
        confidence="verified",
        publication_status="verified",
        semantic_key="service-a-visible-pattern",
    )
    related_block = IdeaBlock(id="block_2", domain_id="domain_1")
    related_revision = IdeaBlockRevision(
        id="rev_2",
        idea_block_id="block_2",
        revision_number=1,
        statement="A related architecture proposition.",
        normalized_statement="a related architecture proposition",
        search_text="a related architecture proposition",
        type="architecture",
        confidence="verified",
        publication_status="verified",
        semantic_key="related-architecture-proposition",
    )
    session.add_all([block, revision, related_block, related_revision])
    session.flush()
    block.current_revision_id = revision.id
    related_block.current_revision_id = related_revision.id
    session.add(IdeaBlockEntity(idea_block_id=block.id, entity_id="entity_1", role="subject"))
    session.add(IdeaBlockEntity(idea_block_id=related_block.id, entity_id="entity_2", role="subject"))
    risk = GovernanceRecord(
        id="risk_1",
        business_key="RISK-1",
        record_type="risk",
        title="Risk one",
        domain_id="domain_1",
        lifecycle_status="identified",
    )
    risk_revision = GovernanceRecordRevision(
        id="risk_rev_1",
        governance_record_id=risk.id,
        revision_number=1,
        snapshot={},
        transition_reason="created",
        actor="tester",
        process="test",
    )
    session.add_all([risk, risk_revision])
    session.flush()
    risk.current_revision_id = risk_revision.id
    session.add(GovernanceRecordIdeaBlock(governance_record_id=risk.id, idea_block_id=block.id, role="governs"))
    session.add_all(
        [
            ClaimEvidence(
                id="evidence_visible_support",
                idea_block_id=block.id,
                idea_block_revision_id=revision.id,
                source_element_id="element_visible",
                source_quote="Visible supporting quote",
                quote_char_start=0,
                quote_char_end=24,
                confidence="verified",
                extraction_method="test",
                polarity="supports",
            ),
            ClaimEvidence(
                id="evidence_visible_refute",
                idea_block_id=block.id,
                idea_block_revision_id=revision.id,
                source_element_id="element_visible",
                source_quote="Visible refuting quote",
                quote_char_start=30,
                quote_char_end=52,
                confidence="verified",
                extraction_method="test",
                polarity="refutes",
            ),
            ClaimEvidence(
                id="evidence_restricted",
                idea_block_id=block.id,
                idea_block_revision_id=revision.id,
                source_element_id="element_restricted",
                source_quote="Restricted quote",
                confidence="verified",
                extraction_method="test",
            ),
            ClaimEvidence(
                id="evidence_governance",
                idea_block_id=block.id,
                idea_block_revision_id=revision.id,
                governance_record_revision_id=risk_revision.id,
                source_quote="Governance supporting quote",
                confidence="verified",
                extraction_method="test",
            ),
            ClaimEvidence(
                id="evidence_related",
                idea_block_id=related_block.id,
                idea_block_revision_id=related_revision.id,
                source_element_id="element_visible",
                source_quote="Related supporting quote",
                confidence="verified",
                extraction_method="test",
            ),
        ]
    )
    session.add(
        IdeaBlockRelation(
            id="relation_1",
            source_idea_block_id=block.id,
            target_idea_block_id=related_block.id,
            relation_type="contradicts",
            direction="symmetric",
            status="verified",
            decision_provenance={"action": "test"},
        )
    )
    session.add(
        EntityRelationship(
            id="entity_relation_1",
            source_entity_id="entity_1",
            target_entity_id="entity_2",
            relation_type="depends_on",
            confidence="verified",
            status="verified",
        )
    )
    session.add(
        RelationshipProvenance(
            id="relationship_provenance_1",
            relationship_id="entity_relation_1",
            idea_block_id=block.id,
            source_element_id="element_visible",
            source_quote="Visible supporting quote",
            confidence="verified",
            extraction_method="test",
            status="verified",
        )
    )
    session.commit()


class _CaptureRows:
    def all(self) -> list[tuple[str, float]]:
        return []


class _CaptureSession:
    statement = None

    def execute(self, statement):
        self.statement = statement
        return _CaptureRows()
