from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from margin_db.base import Base
from margin_db.entity_models import Entity
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockRevision
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement, SourcePage
from margin_db.user_models import Domain
from margin_eval.gold_corpus import GoldClaimExpectation, GoldCorpus, GoldFixture, GoldRelationshipExpectation
from margin_eval.surface_quality import evaluate_surface_quality
from margin_eval.thresholds import ReleaseStage, surface_threshold_failures


def _seed(session: Session) -> None:
    session.add_all(
        (
            Domain(id="payments", name="Payments"),
            Domain(id="hiring", name="Hiring"),
            Entity(
                id="entity-category-design",
                preferred_label="Design Document",
                normalized_label="design document",
                ontology_type="category",
                domain_id=None,
                lifecycle_status="active",
            ),
            SourceDocument(
                id="source-1",
                title="Payment architecture",
                object_key="payment.md",
                media_type="text/markdown",
                sha256="a" * 64,
                size_bytes=10,
                domain_id="payments",
                category_id="entity-category-design",
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
                id="element-1",
                source_version_id="source-version-1",
                source_page_id="source-page-1",
                native_element_id="paragraph-1",
                kind="paragraph",
                ordinal=0,
                text="The payment API writes settled payments to the ledger.",
            ),
            Entity(
                id="entity-payment-api",
                preferred_label="Payment API",
                normalized_label="payment api",
                ontology_type="service",
                domain_id="payments",
                lifecycle_status="active",
            ),
            Entity(
                id="entity-ledger",
                preferred_label="Ledger",
                normalized_label="ledger",
                ontology_type="data_store",
                domain_id="payments",
                lifecycle_status="active",
            ),
            Entity(
                id="entity-hiring-tool",
                preferred_label="Hiring Tool",
                normalized_label="hiring tool",
                ontology_type="service",
                domain_id="hiring",
                lifecycle_status="active",
            ),
            IdeaBlock(id="block-1", domain_id="payments"),
            IdeaBlockRevision(
                id="revision-1",
                idea_block_id="block-1",
                revision_number=1,
                statement="The payment API writes settled payments to the ledger.",
                normalized_statement="the payment api writes settled payments to the ledger",
                type="claim",
                confidence="verified",
                publication_status="verified",
                semantic_key="payment-api-writes-settled-payments",
            ),
            ClaimEvidence(
                id="evidence-1",
                idea_block_id="block-1",
                idea_block_revision_id="revision-1",
                source_element_id="element-1",
                source_quote="The payment API writes settled payments to the ledger.",
                confidence="verified",
                extraction_method="test",
                status="active",
            ),
            EntityRelationship(
                id="relationship-1",
                source_entity_id="entity-payment-api",
                target_entity_id="entity-ledger",
                relation_type="uses",
                confidence="verified",
                status="verified",
            ),
            RelationshipProvenance(
                id="provenance-1",
                relationship_id="relationship-1",
                idea_block_id="block-1",
                source_element_id="element-1",
                confidence="verified",
                extraction_method="test",
                source_quote="The payment API writes settled payments to the ledger.",
                status="verified",
            ),
        )
    )
    block = session.get(IdeaBlock, "block-1")
    assert block is not None
    block.current_revision_id = "revision-1"
    session.flush()


def _corpus() -> GoldCorpus:
    fixture = GoldFixture(
        fixture_id="surface-payments",
        document_class="design_document",
        path=__import__("pathlib").Path("tests/fixtures/gold_corpus/customer-platform-v1.md"),
        domain_id="payments",
        category="design document",
        sensitivity="internal",
        expected_assertions=("The payment API writes settled payments to the ledger.",),
        claims=(
            GoldClaimExpectation(
                id="payment_api_writes_settled_payments",
                statement="The payment API writes settled payments to the ledger.",
                claim_type="claim",
            ),
        ),
        relationships=(
            GoldRelationshipExpectation(
                id="payment_api_uses_ledger", source_name="Payment API", target_name="Ledger", relation_type="uses"
            ),
        ),
    )
    return GoldCorpus(version="margin-knowledge-gold-v3", fixtures=(fixture,), evaluation_modes=("deterministic",))


def _corpus_without_relationships() -> GoldCorpus:
    corpus = _corpus()
    fixture = corpus.fixtures[0]
    return GoldCorpus(
        version=corpus.version,
        fixtures=(
            GoldFixture(
                fixture_id=fixture.fixture_id,
                document_class=fixture.document_class,
                path=fixture.path,
                domain_id=fixture.domain_id,
                category=fixture.category,
                sensitivity=fixture.sensitivity,
                expected_assertions=fixture.expected_assertions,
                claims=fixture.claims,
            ),
        ),
        evaluation_modes=corpus.evaluation_modes,
    )


def _corpus_with_claim(statement: str) -> GoldCorpus:
    corpus = _corpus()
    fixture = corpus.fixtures[0]
    return GoldCorpus(
        version=corpus.version,
        fixtures=(
            GoldFixture(
                fixture_id=fixture.fixture_id,
                document_class=fixture.document_class,
                path=fixture.path,
                domain_id=fixture.domain_id,
                category=fixture.category,
                sensitivity=fixture.sensitivity,
                expected_assertions=fixture.expected_assertions,
                claims=(GoldClaimExpectation(id="corrupted_claim", statement=statement, claim_type="claim"),),
            ),
        ),
        evaluation_modes=corpus.evaluation_modes,
    )


def test_the_surfaces_are_measured_against_the_corpus() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        _seed(session)
        metrics = evaluate_surface_quality(session, _corpus())

    assert metrics.graph_edges_expected == 1
    assert metrics.graph_edges_found == 1
    assert metrics.graph_traversal_accuracy == 1.0
    assert metrics.narrative_claims_expected == 1
    assert metrics.narrative_claims_stated == 1
    assert metrics.narrative_stray_statements == 0
    assert metrics.narrative_faithfulness == 1.0
    assert metrics.export_records_expected > 0
    assert metrics.export_records_present == metrics.export_records_expected
    assert metrics.export_records_unexpected == 0
    assert metrics.export_completeness == 1.0
    assert metrics.personal_checks >= 2
    assert metrics.personal_leaks == 0
    assert metrics.personal_isolation == 1.0


def test_a_property_with_no_expectations_does_not_pass() -> None:
    """Nothing measured is not a pass."""

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        _seed(session)
        metrics = evaluate_surface_quality(session, _corpus_without_relationships())

    assert metrics.graph_edges_expected == 0
    assert metrics.graph_traversal_accuracy == 0.0
    assert surface_threshold_failures(metrics, ReleaseStage.MVP) != ()


def test_the_narrative_is_measured_against_the_corpus_claims() -> None:
    """A claim the corpus does not record is not covered by the narrative."""

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        _seed(session)
        metrics = evaluate_surface_quality(session, _corpus_with_claim("Nothing the corpus records."))

    assert metrics.narrative_claims_expected == 1
    assert metrics.narrative_claims_stated == 0
    assert metrics.narrative_faithfulness == 0.0


def test_the_surface_thresholds_are_enforced() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        _seed(session)
        metrics = evaluate_surface_quality(session, _corpus())

    assert surface_threshold_failures(metrics, ReleaseStage.MVP) == ()
    assert surface_threshold_failures(metrics, ReleaseStage.PRODUCTION) == ()
