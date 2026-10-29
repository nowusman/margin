from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from margin_db.base import Base
from margin_db.distillation import create_semantic_review_tasks
from margin_db.idea_block_resolution import create_idea_block_revision, resolve_exact_idea_block_candidate
from margin_db.knowledge_models import ClaimEvidence, EmbeddingRecord, IdeaBlock
from margin_db.review_bundles import build_review_bundle
from margin_db.review_models import ReviewTask
from margin_db.semantic_resolution import (
    SEMANTIC_KEY_VERSION,
    SemanticResolutionResult,
    canonical_semantic_key,
    classify_semantic_pair,
    compare_protected_fields,
)
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement
from margin_db.user_models import Domain
from margin_schema.extraction import ExtractedIdeaBlock
from tests.helpers.catalog import catalog_entity
from tests.helpers.semantic_candidates import sqlite_semantic_candidate_retriever


@dataclass
class RevisionView:
    statement: str
    normalized_statement: str
    type: str = "fact"
    polarity: str = "supports"
    qualifiers: dict = None  # type: ignore[assignment]
    valid_from: date | None = None
    valid_to: date | None = None
    metadata_: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.qualifiers = self.qualifiers or {}
        self.metadata_ = self.metadata_ or {}


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    session.add(Domain(id="retail", name="Retail"))
    session.add(catalog_entity(entity_id="category", preferred_label="Design", ontology_type="category"))
    session.add(
        SourceDocument(
            id="document",
            title="Semantic test source",
            object_key="semantic-test",
            media_type="text/plain",
            sha256="a" * 64,
            domain_id="retail",
            category_id="category",
        )
    )
    session.add(SourceDocumentVersion(id="version", source_document_id="document", version_sha256="b" * 64))
    session.add_all(
        [
            SourceElement(
                id="elem_a",
                source_version_id="version",
                kind="paragraph",
                ordinal=0,
                text="Orders use PostgreSQL.",
                char_start=0,
                char_end=22,
            ),
            SourceElement(
                id="elem_b",
                source_version_id="version",
                kind="paragraph",
                ordinal=1,
                text="Orders use PostgreSQL.",
                char_start=23,
                char_end=45,
            ),
        ]
    )
    session.flush()
    return session


@pytest.fixture(autouse=True)
def sqlite_semantic_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("margin_db.distillation.retrieve_semantic_candidates", sqlite_semantic_candidate_retriever)


def _candidate(
    *, element_id: str, statement: str, entities: list[dict] | None = None, quote_start: int = 0
) -> ExtractedIdeaBlock:
    return ExtractedIdeaBlock(
        statement=statement,
        type="fact",
        confidence="extracted",
        evidence=[
            {
                "source_element_id": element_id,
                "source_quote": statement,
                "quote_char_start": quote_start,
                "quote_char_end": quote_start + len(statement),
            }
        ],
        entities=entities or [],
    )


def test_semantic_key_sorts_qualifiers_and_entity_ids_but_preserves_scope() -> None:
    common = {
        "normalized_statement": "Orders use PostgreSQL",
        "type": "fact",
        "polarity": "supports",
        "valid_from": date(2026, 1, 1),
        "valid_to": None,
    }
    first = canonical_semantic_key(
        domain_id="retail", qualifiers={"scope": ["b", "a"]}, resolved_entity_ids=["ent_b", "ent_a"], **common
    )
    second = canonical_semantic_key(
        domain_id="retail", qualifiers={"scope": ["a", "b"]}, resolved_entity_ids=["ent_a", "ent_b"], **common
    )
    assert first == second
    assert first != canonical_semantic_key(domain_id="finance", qualifiers={}, resolved_entity_ids=[], **common)


def test_exact_resolution_attaches_independent_evidence_to_one_block() -> None:
    with _session() as session:
        first = resolve_exact_idea_block_candidate(
            session, _candidate(element_id="elem_a", statement="Orders use PostgreSQL."), domain_id="retail"
        )
        second = resolve_exact_idea_block_candidate(
            session,
            _candidate(element_id="elem_b", statement="Orders use PostgreSQL.", quote_start=23),
            domain_id="retail",
        )

        assert second.attached_existing
        assert first.block.id == second.block.id
        assert session.scalars(select(IdeaBlock)).all() == [first.block]
        assert len(session.scalars(select(ClaimEvidence)).all()) == 2
        assert first.revision.semantic_key_version == SEMANTIC_KEY_VERSION


def test_exact_resolution_does_not_attach_to_tombstoned_block() -> None:
    with _session() as session:
        first = resolve_exact_idea_block_candidate(
            session, _candidate(element_id="elem_a", statement="Orders use PostgreSQL."), domain_id="retail"
        )
        first.block.tombstoned_at = datetime(2026, 1, 1)
        first.block.tombstone_reason = "retired source contract"
        session.flush()

        with pytest.raises(ValueError, match="archived or tombstoned"):
            resolve_exact_idea_block_candidate(
                session,
                _candidate(element_id="elem_b", statement="Orders use PostgreSQL.", quote_start=23),
                domain_id="retail",
            )

        assert len(session.scalars(select(ClaimEvidence)).all()) == 1


def test_protected_fields_block_equivalence_before_classifier_call() -> None:
    calls: list[str] = []

    class Classifier:
        model_version = "test-model"

        def classify(self, **kwargs):
            calls.append("called")
            return {"outcome": "equivalent", "rationale": "unsafe"}

    left = RevisionView("TLS is required for 30 days.", "tls is required for 30 days.")
    right = RevisionView("TLS is not required for 30 days.", "tls is not required for 30 days.")
    comparison = compare_protected_fields(left, right)
    result = classify_semantic_pair(left, right, comparison=comparison, classifier=Classifier(), score=0.99)

    assert "negation" in comparison.differences
    assert result.outcome == "uncertain"
    assert calls == []


def test_classifier_result_is_a_suggestion_and_invalid_output_is_uncertain() -> None:
    left = RevisionView("Orders use PostgreSQL.", "orders use postgresql.")
    right = RevisionView("Orders rely on PostgreSQL.", "orders rely on postgresql.")

    class Classifier:
        model_version = "test-model-v1"

        def __init__(self, response):
            self.response = response

        def classify(self, **kwargs):
            return self.response

    suggested = classify_semantic_pair(
        left, right, classifier=Classifier({"outcome": "related", "rationale": "same subject", "score": 0.8})
    )
    invalid = classify_semantic_pair(left, right, classifier=Classifier({"outcome": "merge", "score": 4}))

    assert isinstance(suggested, SemanticResolutionResult)
    assert suggested.outcome == "related"
    assert suggested.model_version == "test-model-v1"
    assert invalid.outcome == "uncertain"


def test_semantic_batch_persists_structured_review_bundle_with_bounded_test_pool() -> None:
    with _session() as session:
        revisions = []
        for block_id, statement in (("ib_left", "Orders use PostgreSQL."), ("ib_right", "Orders rely on PostgreSQL.")):
            block = IdeaBlock(id=block_id, domain_id="retail")
            session.add(block)
            session.flush()
            revision = create_idea_block_revision(
                session,
                block,
                statement=statement,
                normalized_statement=statement.casefold(),
                type="fact",
                confidence="extracted",
                polarity="supports",
                qualifiers={},
                valid_from=None,
                valid_to=None,
                publication_status="candidate",
                actor="test",
                process="test",
            )
            revisions.append(revision)
        vector = [1.0] + [0.0] * 383
        session.add_all(
            [
                EmbeddingRecord(
                    id="emb_left",
                    target_type="idea_block_revision",
                    target_id=revisions[0].id,
                    model_id="test-embedding-model-v1",
                    index_version="test-embedding-index-v1",
                    embedding=vector,
                    content_hash="left",
                    provider_id="test",
                    dimensions=384,
                ),
                EmbeddingRecord(
                    id="emb_right",
                    target_type="idea_block_revision",
                    target_id=revisions[1].id,
                    model_id="test-embedding-model-v1",
                    index_version="test-embedding-index-v1",
                    embedding=vector,
                    content_hash="right",
                    provider_id="test",
                    dimensions=384,
                ),
            ]
        )
        candidates = create_semantic_review_tasks(session, candidate_limit=1, max_candidates=1)
        task = session.scalar(select(ReviewTask))
        bundle = build_review_bundle(session, task.id) if task is not None else {}

        assert len(candidates) == 1
        assert candidates[0].protected_field_differences == {}
        assert candidates[0].classification.outcome == "uncertain"
        assert task is not None and task.task_type == "semantic_resolution_review"
        assert bundle["semantic_resolution"]["suggested_outcome"] == "uncertain"
        assert len(bundle["claims"]) == 2
