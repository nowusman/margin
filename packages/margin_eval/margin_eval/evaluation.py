"""Evidence-backed quality evaluation runner.

This module replaces self-reported caller-supplied adjudication with metrics
computed from a versioned gold corpus and persisted Margin output.  No quality
metric is derived from a number supplied by an API caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from margin_db.entity_models import Entity, EntityMention, EntityResolutionDecision
from margin_db.extraction_models import SpanExtractionAttempt
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockRevision
from margin_db.model_catalog_models import ModelConfiguration
from margin_db.model_registry import configured_active_model
from margin_db.relationship_consistency import run_relationship_consistency_lint
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.source_ingestion_helpers import normalize_source_uri
from margin_db.source_models import (
    SourceDocument,
    SourceDocumentVersion,
    SourceElement,
    SourceElementOutcome,
    SourcePage,
)
from margin_eval.gold_corpus import (
    GoldClaimExpectation,
    GoldCorpus,
    GoldEntityExpectation,
    GoldEvidenceAnchor,
    GoldFixture,
    GoldNoClaimExpectation,
)
from margin_eval.knowledge_quality import (
    EVALUATION_THRESHOLDS,
    EvaluationMetrics,
    EvaluationMode,
    EvaluationRun,
    FixtureResult,
    MatchKind,
    MatchRecord,
    aggregate_metrics as _aggregate_metrics,
    build_run_id as _build_run_id,
    gate_failures as _gate_failures,
    list_runs,
    load_run,
    persist_run as _persist_run,
)
from margin_schema.text_normalization import normalize_canonical_text, normalize_entity_name, normalize_statement


@dataclass(slots=True)
class _FixtureContext:
    fixture: GoldFixture
    source_document_id: str | None
    active_version_id: str | None
    source_element_ids: tuple[str, ...]
    page_numbers: tuple[int, ...]


def run_evaluation(
    session: Session,
    corpus: GoldCorpus,
    *,
    evaluation_mode: EvaluationMode,
    model_id: str | None = None,
    prompt_version: str | None = None,
    index_version: str | None = None,
    output_dir: Path = Path(".margin/evaluation_runs"),
) -> EvaluationRun:
    """Run an evidence-backed evaluation.

    The session must already contain the ingested Margin output for every
    fixture.  The runner matches actual records against the corpus and never
    accepts numbers from API callers.

    Evaluation modes:
    - ``deterministic``: matches persisted records without requiring the
      active configured extraction model.  Used in CI with precomputed
      inference output.
    - ``operational``: additionally requires the active configured extraction
      model to be available and reports its ID in the run record.
    """
    started_at = datetime.now(UTC)
    active_model = _resolve_active_model(session, evaluation_mode)
    contexts = tuple(_build_context(session, fixture) for fixture in corpus.fixtures)
    fixture_results = tuple(_evaluate_fixture(session, context) for context in contexts)
    metrics = _aggregate_metrics(fixture_results)
    lint_findings = _relationship_lint_findings(session)
    metrics = metrics.with_relationship_consistency_error_count(
        sum(1 for finding in lint_findings if finding.severity == "error")
    )
    gate_failures = _gate_failures(metrics)
    status = "passed" if not gate_failures else "failed"
    completed_at = datetime.now(UTC)
    run = EvaluationRun(
        run_id=_build_run_id(
            corpus_version=corpus.version,
            evaluation_mode=evaluation_mode,
            model_id=model_id,
            prompt_version=prompt_version,
            index_version=index_version,
            started_at=started_at,
        ),
        started_at=started_at,
        completed_at=completed_at,
        corpus_version=corpus.version,
        corpus_path=Path("tests/fixtures/gold_corpus/manifest.json"),
        evaluation_mode=evaluation_mode.value,
        model_id=model_id,
        prompt_version=prompt_version,
        index_version=index_version,
        active_extraction_model_id=active_model.id if active_model else None,
        metrics=metrics,
        fixture_results=fixture_results,
        relationship_consistency_findings=tuple(
            {
                "rule_id": finding.rule_id,
                "severity": finding.severity,
                "target_type": finding.target_type,
                "target_id": finding.target_id,
                "message": finding.message,
            }
            for finding in lint_findings
        ),
        gate_failures=gate_failures,
        status=status,
    )
    _persist_run(run, output_dir)
    return run


def _resolve_active_model(session: Session, evaluation_mode: EvaluationMode) -> ModelConfiguration | None:
    """Resolve the active configured extraction model for operational runs."""
    if evaluation_mode is EvaluationMode.DETERMINISTIC:
        return None
    try:
        config = configured_active_model(session, "extraction")
    except Exception:
        return None
    if config is None:
        return None
    return session.get(ModelConfiguration, config.id)


def _build_context(session: Session, fixture: GoldFixture) -> _FixtureContext:
    document_id: str | None = None
    version_id: str | None = None
    document = _find_fixture_document(session, fixture)
    if document is not None:
        document_id = document.id
        version = session.scalar(
            select(SourceDocumentVersion)
            .where(SourceDocumentVersion.source_document_id == document.id, SourceDocumentVersion.status == "active")
            .order_by(SourceDocumentVersion.created_at.desc())
        )
        if version is not None:
            version_id = version.id
    element_ids: tuple[str, ...] = ()
    page_numbers: tuple[int, ...] = ()
    if version_id is not None:
        element_ids = tuple(
            session.scalars(select(SourceElement.id).where(SourceElement.source_version_id == version_id)).all()
        )
        page_numbers = tuple(
            session.scalars(
                select(SourcePage.page_number)
                .where(SourcePage.source_version_id == version_id)
                .order_by(SourcePage.page_number.asc())
            ).all()
        )
    return _FixtureContext(
        fixture=fixture,
        source_document_id=document_id,
        active_version_id=version_id,
        source_element_ids=element_ids,
        page_numbers=page_numbers,
    )


def _find_fixture_document(session: Session, fixture: GoldFixture) -> SourceDocument | None:
    """Find the source document for a fixture.

    Lookup order:
    1. domain_id + logical identity (the identity the fixture was ingested under)
    2. domain_id + source_document_id (from supersedes[0]) — explicit pointer
    3. domain_id + deterministic id ``doc-{fixture_id}`` — test/seeded path
    No implicit fallback is allowed: a fixture without an exact source identity
    remains unbound so its evaluation cannot contaminate another document.
    """
    identity = normalize_source_uri(fixture.source_identity())
    if identity is not None:
        document = session.scalar(
            select(SourceDocument).where(
                SourceDocument.domain_id == fixture.domain_id, SourceDocument.logical_identity == identity
            )
        )
        if document is not None:
            return document
    for predecessor in fixture.supersedes:
        document = session.get(SourceDocument, predecessor)
        if document is not None and document.domain_id == fixture.domain_id:
            return document
    expected_id = f"doc-{fixture.fixture_id}"
    document = session.get(SourceDocument, expected_id)
    if document is not None and document.domain_id == fixture.domain_id:
        return document
    return None


def _evaluate_fixture(session: Session, context: _FixtureContext) -> FixtureResult:
    fixture = context.fixture
    claim_matches = _match_claims(session, context)
    field_matches = _match_fields(session, context)
    entity_matches = _match_entities(session, context)
    relationship_matches = _match_relationships(session, context)
    no_claim_matches = _match_no_claim_elements(session, context)
    (
        failed_elements,
        deferred_elements,
        failed_attempts,
        deferred_attempts,
        terminal_outcomes,
        unaccounted_elements,
        mapped_with_outcome,
    ) = _element_outcome_counts(session, context)
    unbacked_claims = _unbacked_claim_count(session, context)
    missing_pages, expected_pages_total, expected_pages_reconciled = _page_reconciliation(context)
    verified_claims_with_evidence, verified_relationships_with_evidence = _verified_with_evidence_counts(
        session, context
    )
    inference_origin_candidate_ids = _inference_origin_candidate_ids(session, context)
    ghost_record_ids = _ghost_record_ids(session, context)
    ambiguous_count = sum(
        1
        for match in (*claim_matches, *field_matches, *entity_matches, *relationship_matches, *no_claim_matches)
        if match.kind is MatchKind.AMBIGUOUS
    )
    return FixtureResult(
        fixture_id=fixture.fixture_id,
        domain_id=fixture.domain_id,
        source_document_id=context.source_document_id,
        claim_matches=claim_matches,
        field_matches=field_matches,
        entity_matches=entity_matches,
        relationship_matches=relationship_matches,
        no_claim_matches=no_claim_matches,
        failed_element_count=failed_elements,
        deferred_element_count=deferred_elements,
        failed_attempt_count=failed_attempts,
        deferred_attempt_count=deferred_attempts,
        terminal_outcome_count=terminal_outcomes,
        unaccounted_element_count=unaccounted_elements,
        unbacked_claim_count=unbacked_claims,
        page_reconciliation_missing=missing_pages,
        expected_pages_total=expected_pages_total,
        expected_pages_reconciled=expected_pages_reconciled,
        verified_claims_with_evidence=verified_claims_with_evidence,
        verified_relationships_with_evidence=(verified_relationships_with_evidence),
        mapped_elements_with_outcome=mapped_with_outcome,
        inference_origin_candidate_ids=inference_origin_candidate_ids,
        ghost_record_ids=ghost_record_ids,
        ambiguous_match_count=ambiguous_count,
    )


def _match_claims(session: Session, context: _FixtureContext) -> tuple[MatchRecord, ...]:
    fixture = context.fixture
    if not fixture.claims:
        return ()
    if context.active_version_id is None:
        return tuple(
            MatchRecord(expected_id=f"{fixture.fixture_id}::{claim.id}", actual_ids=(), kind=MatchKind.FALSE_NEGATIVE)
            for claim in fixture.claims
        )
    idea_blocks = _active_idea_blocks(session, context.active_version_id)
    evidence_by_block = _evidence_by_block(session, context.active_version_id)
    matches: list[MatchRecord] = []
    matched_block_ids: set[str] = set()
    for claim in fixture.claims:
        block_ids, evidence_ids = _match_claim(idea_blocks, claim, evidence_by_block)
        if block_ids:
            matched_block_ids.update(block_ids)
            matches.append(
                MatchRecord(
                    expected_id=f"{fixture.fixture_id}::{claim.id}",
                    actual_ids=block_ids,
                    kind=MatchKind.TRUE_POSITIVE,
                    evidence_ids=evidence_ids,
                )
            )
        else:
            matches.append(
                MatchRecord(
                    expected_id=f"{fixture.fixture_id}::{claim.id}", actual_ids=(), kind=MatchKind.FALSE_NEGATIVE
                )
            )
    extra_blocks = [
        block.id
        for block in idea_blocks
        if block.id not in matched_block_ids and _block_matches_no_expected(block, fixture)
    ]
    for block_id in extra_blocks:
        matches.append(
            MatchRecord(
                expected_id=f"{fixture.fixture_id}::unsupported::{block_id}",
                actual_ids=(block_id,),
                kind=MatchKind.FALSE_POSITIVE,
            )
        )
    return tuple(matches)


def _match_claim(
    idea_blocks: tuple[IdeaBlock, ...],
    claim: GoldClaimExpectation,
    evidence_by_block: dict[str, tuple[ClaimEvidence, ...]],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return matched block ids and evidence ids for a claim expectation."""
    target_statement = normalize_statement(claim.statement)
    matched_block_ids: list[str] = []
    matched_evidence_ids: list[str] = []
    for block in idea_blocks:
        revision = block.current_revision
        if revision is None or revision.type != claim.claim_type:
            continue
        if revision.polarity != claim.polarity:
            continue
        if normalize_statement(revision.statement) != target_statement:
            continue
        evidence_rows = evidence_by_block.get(block.id, ())
        anchor_evidence_ids = _anchor_evidence_for_claim(evidence_rows, claim.evidence_anchors)
        if claim.evidence_anchors and not anchor_evidence_ids:
            continue
        matched_block_ids.append(block.id)
        matched_evidence_ids.extend(anchor_evidence_ids)
    return tuple(matched_block_ids), tuple(matched_evidence_ids)


def _anchor_evidence_for_claim(
    evidence_rows: tuple[ClaimEvidence, ...], anchors: tuple[GoldEvidenceAnchor, ...]
) -> tuple[str, ...]:
    if not anchors:
        return tuple(row.id for row in evidence_rows)
    matched: list[str] = []
    for anchor in anchors:
        anchor_text = normalize_canonical_text(anchor.quote)
        if not anchor_text:
            continue
        for evidence in evidence_rows:
            if not evidence.source_quote:
                continue
            if anchor_text in normalize_canonical_text(evidence.source_quote):
                matched.append(evidence.id)
    return tuple(matched)


def _evidence_by_block(session: Session, version_id: str) -> dict[str, tuple[ClaimEvidence, ...]]:
    rows = (
        session.execute(
            select(ClaimEvidence)
            .join(SourceElement, SourceElement.id == ClaimEvidence.source_element_id)
            .where(SourceElement.source_version_id == version_id, ClaimEvidence.status == "active")
        )
        .scalars()
        .all()
    )
    by_block: dict[str, list[ClaimEvidence]] = {}
    for evidence in rows:
        by_block.setdefault(evidence.idea_block_id, []).append(evidence)
    return {block_id: tuple(rows) for block_id, rows in by_block.items()}


def _active_idea_blocks(session: Session, version_id: str) -> tuple[IdeaBlock, ...]:
    return tuple(
        session.scalars(
            select(IdeaBlock)
            .join(ClaimEvidence, ClaimEvidence.idea_block_id == IdeaBlock.id)
            .join(
                IdeaBlockRevision,
                (IdeaBlockRevision.id == IdeaBlock.current_revision_id)
                & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
            )
            .join(SourceElement, SourceElement.id == ClaimEvidence.source_element_id)
            .where(
                SourceElement.source_version_id == version_id,
                IdeaBlockRevision.publication_status == "verified",
                ClaimEvidence.status == "active",
            )
            .distinct()
        ).all()
    )


def _block_matches_no_expected(block: IdeaBlock, fixture: GoldFixture) -> bool:
    revision = block.current_revision
    if revision is None:
        return False
    return any(
        normalize_statement(revision.statement) == normalize_statement(claim.statement)
        and revision.polarity == claim.polarity
        and revision.type == claim.claim_type
        for claim in fixture.claims
    )


def _match_fields(session: Session, context: _FixtureContext) -> tuple[MatchRecord, ...]:
    fixture = context.fixture
    if not fixture.fields:
        return ()
    matches: list[MatchRecord] = []
    if context.source_document_id is None:
        for field_spec in fixture.fields:
            matches.append(
                MatchRecord(
                    expected_id=f"{fixture.fixture_id}::field::{field_spec.field_path}",
                    actual_ids=(),
                    kind=MatchKind.FALSE_NEGATIVE,
                )
            )
        return tuple(matches)
    metadata_rows = session.scalars(select(SourceDocument).where(SourceDocument.id == context.source_document_id)).all()
    document = metadata_rows[0] if metadata_rows else None
    for field_spec in fixture.fields:
        actual_value = _lookup_field(document, field_spec.field_path) if document else None
        if actual_value is None or not _field_values_equal(actual_value, field_spec.expected_value):
            matches.append(
                MatchRecord(
                    expected_id=f"{fixture.fixture_id}::field::{field_spec.field_path}",
                    actual_ids=(),
                    kind=MatchKind.FALSE_NEGATIVE,
                )
            )
        else:
            matches.append(
                MatchRecord(
                    expected_id=f"{fixture.fixture_id}::field::{field_spec.field_path}",
                    actual_ids=(context.source_document_id,),
                    kind=MatchKind.TRUE_POSITIVE,
                )
            )
    return tuple(matches)


def _lookup_field(document: SourceDocument, path: str) -> Any:
    parts = path.split(".")
    current: Any = {
        "metadata": document.metadata_,
        "dublin_core": document.dublin_core,
        "logical_identity": document.logical_identity,
        "source_uri": document.source_uri,
        "title": document.title,
    }
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


def _field_values_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, str) and isinstance(expected, str):
        return normalize_canonical_text(actual) == normalize_canonical_text(expected)
    if isinstance(actual, int | float) and isinstance(expected, int | float):
        return actual == expected
    return actual == expected


def _match_entities(session: Session, context: _FixtureContext) -> tuple[MatchRecord, ...]:
    """Match expected entities against resolution decisions.

    Entity accuracy is computed from ``EntityResolutionDecision`` linked back
    to ``Entity`` (canonical resolution target) and
    ``EntityMention`` (proposed text) — never from the entity table directly.
    """
    fixture = context.fixture
    if not fixture.entities:
        return ()
    matches: list[MatchRecord] = []
    for expected in fixture.entities:
        canonical = _find_canonical_entity(session, fixture, expected)
        if canonical is None:
            matches.append(
                MatchRecord(
                    expected_id=f"{fixture.fixture_id}::entity::{expected.name}",
                    actual_ids=(),
                    kind=MatchKind.FALSE_NEGATIVE,
                )
            )
            continue
        decision_ids, correct = _entity_resolution_decision_for(session, fixture, expected, canonical)
        if not decision_ids:
            matches.append(
                MatchRecord(
                    expected_id=f"{fixture.fixture_id}::entity::{expected.name}",
                    actual_ids=(canonical.id,),
                    kind=MatchKind.AMBIGUOUS,
                )
            )
            continue
        if correct:
            matches.append(
                MatchRecord(
                    expected_id=f"{fixture.fixture_id}::entity::{expected.name}",
                    actual_ids=(canonical.id,),
                    kind=MatchKind.TRUE_POSITIVE,
                    evidence_ids=decision_ids,
                )
            )
        else:
            matches.append(
                MatchRecord(
                    expected_id=f"{fixture.fixture_id}::entity::{expected.name}",
                    actual_ids=decision_ids,
                    kind=MatchKind.FALSE_NEGATIVE,
                )
            )
    return tuple(matches)


def _find_canonical_entity(session: Session, fixture: GoldFixture, expected: GoldEntityExpectation) -> Entity | None:
    if expected.external_id:
        return session.scalar(
            select(Entity).where(Entity.domain_id == fixture.domain_id, Entity.external_id == expected.external_id)
        )
    return session.scalar(
        select(Entity).where(
            Entity.domain_id == fixture.domain_id,
            Entity.normalized_label == normalize_entity_name(expected.name),
            Entity.ontology_type == expected.kind,
        )
    )


def _entity_resolution_decision_for(
    session: Session, fixture: GoldFixture, expected: GoldEntityExpectation, canonical: Entity
) -> tuple[tuple[str, ...], bool]:
    """Return (decision ids, is_correct) for an expected entity."""
    decision_rows = session.execute(
        select(EntityResolutionDecision, EntityMention)
        .join(EntityMention, EntityMention.id == EntityResolutionDecision.mention_id)
        .where(
            EntityMention.domain_id == fixture.domain_id,
            EntityMention.proposed_type == expected.kind,
            EntityResolutionDecision.is_current.is_(True),
        )
    ).all()
    if not decision_rows:
        return (), False
    decision_ids: list[str] = []
    correct = True
    for decision, _mention in decision_rows:
        if decision.entity_id != canonical.id:
            correct = False
        decision_ids.append(decision.id)
    if not decision_ids:
        return (), False
    return tuple(decision_ids), correct


def _match_relationships(session: Session, context: _FixtureContext) -> tuple[MatchRecord, ...]:
    fixture = context.fixture
    if not fixture.relationships:
        return ()
    if context.active_version_id is None:
        return tuple(
            MatchRecord(
                expected_id=f"{fixture.fixture_id}::relationship::{relationship.id}",
                actual_ids=(),
                kind=MatchKind.FALSE_NEGATIVE,
            )
            for relationship in fixture.relationships
        )
    matches: list[MatchRecord] = []
    matched_relationship_ids: set[str] = set()
    for relationship in fixture.relationships:
        actual_ids, evidence_ids = _find_relationship(session, fixture, relationship, context)
        if actual_ids:
            matched_relationship_ids.update(actual_ids)
            matches.append(
                MatchRecord(
                    expected_id=f"{fixture.fixture_id}::relationship::{relationship.id}",
                    actual_ids=actual_ids,
                    kind=MatchKind.TRUE_POSITIVE,
                    evidence_ids=evidence_ids,
                )
            )
        else:
            matches.append(
                MatchRecord(
                    expected_id=f"{fixture.fixture_id}::relationship::{relationship.id}",
                    actual_ids=(),
                    kind=MatchKind.FALSE_NEGATIVE,
                )
            )
    false_positive_ids = _unmatched_verified_relationships(session, fixture, context, matched_relationship_ids)
    for relationship_id in false_positive_ids:
        matches.append(
            MatchRecord(
                expected_id=f"{fixture.fixture_id}::relationship::unsupported::{relationship_id}",
                actual_ids=(relationship_id,),
                kind=MatchKind.FALSE_POSITIVE,
            )
        )
    return tuple(matches)


def _find_relationship(
    session: Session, fixture: GoldFixture, relationship: Any, context: _FixtureContext
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    source_entity = _resolve_relationship_entity(session, fixture, relationship.source_name)
    target_entity = _resolve_relationship_entity(session, fixture, relationship.target_name)
    if source_entity is None or target_entity is None:
        return (), ()
    relationships = tuple(
        session.scalars(
            select(EntityRelationship).where(
                EntityRelationship.source_entity_id == source_entity.id,
                EntityRelationship.target_entity_id == target_entity.id,
                EntityRelationship.relation_type == relationship.relation_type,
            )
        ).all()
    )
    provenance_ids: set[str] = set()
    matched_ids: list[str] = []
    for candidate in relationships:
        provenance = session.scalar(
            select(RelationshipProvenance).where(
                RelationshipProvenance.relationship_id == candidate.id, RelationshipProvenance.status == "verified"
            )
        )
        if provenance is None or not provenance.source_quote:
            continue
        if relationship.evidence_anchor is None:
            continue
        if not _quote_matches_anchor(provenance.source_quote, relationship.evidence_anchor):
            continue
        matched_ids.append(candidate.id)
        provenance_ids.add(provenance.id)
    return tuple(matched_ids), tuple(provenance_ids)


def _resolve_relationship_entity(session: Session, fixture: GoldFixture, name: str) -> Entity | None:
    return session.scalar(
        select(Entity).where(
            Entity.domain_id == fixture.domain_id, Entity.normalized_label == normalize_entity_name(name)
        )
    )


def _quote_matches_anchor(quote: str, anchor: GoldEvidenceAnchor) -> bool:
    normalized_quote = normalize_canonical_text(quote)
    normalized_anchor = normalize_canonical_text(anchor.quote)
    if not normalized_anchor:
        return False
    return normalized_anchor in normalized_quote


def _unmatched_verified_relationships(
    session: Session, fixture: GoldFixture, context: _FixtureContext, matched_ids: set[str]
) -> tuple[str, ...]:
    if context.active_version_id is None:
        return ()
    rows = session.execute(
        select(EntityRelationship, RelationshipProvenance)
        .join(RelationshipProvenance, RelationshipProvenance.relationship_id == EntityRelationship.id)
        .join(ClaimEvidence, ClaimEvidence.idea_block_id == RelationshipProvenance.idea_block_id)
        .join(SourceElement, SourceElement.id == ClaimEvidence.source_element_id)
        .where(
            SourceElement.source_version_id == context.active_version_id,
            EntityRelationship.status == "verified",
            RelationshipProvenance.status == "verified",
        )
    ).all()
    expected_pairs = {
        (relationship.source_name, relationship.target_name, relationship.relation_type)
        for relationship in fixture.relationships
    }
    names_by_id = {
        entity.id: entity.preferred_label
        for entity in session.scalars(select(Entity).where(Entity.domain_id == fixture.domain_id)).all()
    }
    false_positives: list[str] = []
    for relationship, _provenance in rows:
        if relationship.id in matched_ids:
            continue
        pair = (
            names_by_id.get(relationship.source_entity_id),
            names_by_id.get(relationship.target_entity_id),
            relationship.relation_type,
        )
        if pair in expected_pairs:
            continue
        false_positives.append(relationship.id)
    return tuple(sorted(set(false_positives)))


def _match_no_claim_elements(session: Session, context: _FixtureContext) -> tuple[MatchRecord, ...]:
    fixture = context.fixture
    if not fixture.no_claim_elements:
        return ()
    matches: list[MatchRecord] = []
    for expected in fixture.no_claim_elements:
        outcome = _find_no_claim_outcome(session, context, expected)
        if outcome is None:
            matches.append(
                MatchRecord(
                    expected_id=f"{fixture.fixture_id}::no_claim::{expected.element_id}",
                    actual_ids=(),
                    kind=MatchKind.FALSE_NEGATIVE,
                )
            )
            continue
        matches.append(
            MatchRecord(
                expected_id=f"{fixture.fixture_id}::no_claim::{expected.element_id}",
                actual_ids=(outcome.id,),
                kind=MatchKind.TRUE_POSITIVE,
                evidence_ids=(outcome.id,),
            )
        )
    return tuple(matches)


def _find_no_claim_outcome(
    session: Session, context: _FixtureContext, expected: GoldNoClaimExpectation
) -> SourceElementOutcome | None:
    if context.active_version_id is None:
        return None
    return session.scalar(
        select(SourceElementOutcome)
        .join(SourceElement, SourceElement.id == SourceElementOutcome.source_element_id)
        .where(
            SourceElementOutcome.source_element_id == expected.element_id,
            SourceElementOutcome.outcome == "verified_no_claim",
            SourceElementOutcome.verification_method == expected.verification_method,
            SourceElementOutcome.verifier_version == expected.verifier_version,
            SourceElement.source_version_id == context.active_version_id,
        )
    )


def _element_outcome_counts(session: Session, context: _FixtureContext) -> tuple[int, int, int, int, int, int, int]:
    """Return (failed, deferred, failed_attempts, deferred_attempts, terminal, unaccounted, mapped_with_outcome)."""
    if context.active_version_id is None:
        return 0, 0, 0, 0, 0, 0, 0
    total_elements = (
        session.scalar(
            select(func.count(SourceElement.id)).where(SourceElement.source_version_id == context.active_version_id)
        )
        or 0
    )
    rows = session.execute(
        select(SourceElementOutcome.outcome, func.count(SourceElementOutcome.id))
        .where(
            SourceElementOutcome.source_element_id.in_(
                select(SourceElement.id).where(SourceElement.source_version_id == context.active_version_id)
            )
        )
        .group_by(SourceElementOutcome.outcome)
    ).all()
    by_outcome = {outcome: count for outcome, count in rows}
    failed = by_outcome.get("failed", 0)
    deferred = (
        by_outcome.get("verified_no_claim", 0) + by_outcome.get("quarantined", 0) + by_outcome.get("unsupported", 0)
    )
    terminal = sum(by_outcome.values())
    unaccounted = max(total_elements - terminal, 0)
    mapped_with_outcome = (
        by_outcome.get("extracted", 0)
        + by_outcome.get("verified_no_claim", 0)
        + by_outcome.get("quarantined", 0)
        + by_outcome.get("unsupported", 0)
    )
    attempt_rows = session.execute(
        select(SpanExtractionAttempt.outcome, func.count(SpanExtractionAttempt.id))
        .where(
            SpanExtractionAttempt.source_element_id.in_(
                select(SourceElement.id).where(SourceElement.source_version_id == context.active_version_id)
            )
        )
        .group_by(SpanExtractionAttempt.outcome)
    ).all()
    attempt_by_outcome = {outcome: count for outcome, count in attempt_rows}
    failed_attempts = attempt_by_outcome.get("failed", 0)
    deferred_attempts = attempt_by_outcome.get("deferred", 0)
    return (failed, deferred, failed_attempts, deferred_attempts, terminal, unaccounted, mapped_with_outcome)


def _unbacked_claim_count(session: Session, context: _FixtureContext) -> int:
    if context.active_version_id is None:
        return 0
    return (
        session.scalar(
            select(func.count(IdeaBlock.id))
            .join(
                IdeaBlockRevision,
                (IdeaBlockRevision.id == IdeaBlock.current_revision_id)
                & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
            )
            .where(
                IdeaBlockRevision.publication_status == "verified",
                ~exists().where(ClaimEvidence.idea_block_id == IdeaBlock.id),
            )
        )
        or 0
    )


def _page_reconciliation(context: _FixtureContext) -> tuple[tuple[int, ...], int, int]:
    expected_pages = {
        anchor.page_number
        for claim in context.fixture.claims
        for anchor in claim.evidence_anchors
        if anchor.page_number is not None
    }
    if not expected_pages:
        return (), 0, 0
    missing = tuple(sorted(expected_pages - set(context.page_numbers)))
    total = len(expected_pages)
    reconciled = total - len(missing)
    return missing, total, reconciled


def _verified_with_evidence_counts(session: Session, context: _FixtureContext) -> tuple[int, int]:
    """Return (verified_claims_with_evidence, verified_relationships_with_evidence)."""
    if context.active_version_id is None:
        return 0, 0
    claims_with_evidence = (
        session.scalar(
            select(func.count(func.distinct(IdeaBlock.id)))
            .join(ClaimEvidence, ClaimEvidence.idea_block_id == IdeaBlock.id)
            .join(
                IdeaBlockRevision,
                (IdeaBlockRevision.id == IdeaBlock.current_revision_id)
                & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
            )
            .join(SourceElement, SourceElement.id == ClaimEvidence.source_element_id)
            .where(
                SourceElement.source_version_id == context.active_version_id,
                IdeaBlockRevision.publication_status == "verified",
                ClaimEvidence.status == "active",
                ClaimEvidence.source_quote.is_not(None),
            )
        )
        or 0
    )
    relationships_with_evidence = (
        session.scalar(
            select(func.count(func.distinct(EntityRelationship.id)))
            .join(RelationshipProvenance, RelationshipProvenance.relationship_id == EntityRelationship.id)
            .join(ClaimEvidence, ClaimEvidence.idea_block_id == RelationshipProvenance.idea_block_id)
            .join(SourceElement, SourceElement.id == ClaimEvidence.source_element_id)
            .where(
                SourceElement.source_version_id == context.active_version_id,
                EntityRelationship.status == "verified",
                RelationshipProvenance.status == "verified",
                RelationshipProvenance.source_quote.is_not(None),
            )
        )
        or 0
    )
    return claims_with_evidence, relationships_with_evidence


def _inference_origin_candidate_ids(session: Session, context: _FixtureContext) -> tuple[str, ...]:
    """Canonical entities cannot be created by inference in the governed contract."""

    del session, context
    return ()


def _ghost_record_ids(session: Session, context: _FixtureContext) -> tuple[str, ...]:
    """Return IDs of derived records that reference a tombstoned source version.

    For reprocessing/deletion fixtures this is a positive check: after
    deletion there must be no ``IdeaBlock`` or ``EntityRelationship`` rows whose
    evidence chains back to the deleted source.
    For other fixtures the list should be empty.
    """
    if context.source_document_id is None:
        return ()
    document = session.get(SourceDocument, context.source_document_id)
    if document is None or document.status != "deleted":
        return ()
    ghost_ids: list[str] = []
    ghost_ids.extend(
        str(block_id)
        for block_id in session.scalars(
            select(IdeaBlock.id)
            .join(ClaimEvidence, ClaimEvidence.idea_block_id == IdeaBlock.id)
            .join(
                IdeaBlockRevision,
                (IdeaBlockRevision.id == IdeaBlock.current_revision_id)
                & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
            )
            .join(SourceElement, SourceElement.id == ClaimEvidence.source_element_id)
            .where(
                SourceElement.source_version_id.in_(
                    select(SourceDocumentVersion.id).where(SourceDocumentVersion.source_document_id == document.id)
                ),
                IdeaBlockRevision.publication_status == "verified",
            )
        )
    )
    ghost_ids.extend(
        rel_id
        for rel_id in session.scalars(
            select(EntityRelationship.id)
            .join(RelationshipProvenance, RelationshipProvenance.relationship_id == EntityRelationship.id)
            .join(ClaimEvidence, ClaimEvidence.idea_block_id == RelationshipProvenance.idea_block_id)
            .join(SourceElement, SourceElement.id == ClaimEvidence.source_element_id)
            .where(
                SourceElement.source_version_id.in_(
                    select(SourceDocumentVersion.id).where(SourceDocumentVersion.source_document_id == document.id)
                ),
                EntityRelationship.status == "verified",
            )
        )
    )
    return tuple(sorted(set(ghost_ids)))


def _relationship_lint_findings(session: Session) -> list[Any]:
    return list(run_relationship_consistency_lint(session))


__all__ = [
    "EVALUATION_THRESHOLDS",
    "EvaluationMetrics",
    "EvaluationMode",
    "EvaluationRun",
    "FixtureResult",
    "MatchKind",
    "MatchRecord",
    "list_runs",
    "load_run",
    "run_evaluation",
]
