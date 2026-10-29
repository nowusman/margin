from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from margin_db.audit_models import AuditEvent
from margin_db.base import Base
from margin_db.entity_catalog import (
    EntityMergeConflict,
    add_alias,
    approve_mention_resolution,
    create_entity,
    deprecate_alias,
    resolve_mention,
    schedule_affected_reresolution,
    update_entity,
    upsert_entity_relationship,
)
from margin_db.entity_catalog_jobs import reresolve_affected_entities
from margin_db.entity_catalog_lifecycle import merge_entities
from margin_db.entity_models import Entity, EntityAlias, EntityReresolutionJob, EntityResolutionDecision
from margin_db.idea_block_resolution import create_idea_block_revision
from margin_db.knowledge_models import IdeaBlock, IdeaBlockEntity, IdeaBlockRevision, SearchMaterializationJob
from margin_db.relationship_models import EntityRelationship
from margin_db.review_models import ReviewTask
from margin_db.search_materialization import process_search_materialization_jobs
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement
from margin_db.user_models import Domain, User
from margin_eval.gold_corpus import load_gold_corpus
from margin_schema.ontology import ONTOLOGY_VERSION
from tests.helpers.catalog import catalog_entity


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: object, _record: object) -> None:
        connection.execute("PRAGMA foreign_keys=ON")  # type: ignore[union-attr]

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for domain_id in ("commerce", "billing", "platform"):
            session.add(Domain(id=domain_id, name=domain_id.title()))
        session.add(User(id="reviewer", display_name="Reviewer"))
        session.commit()
        yield session


def _source_revision(session: Session, domain_id: str, suffix: str) -> tuple[str, str]:
    category = create_entity(
        session,
        preferred_label=f"{domain_id.title()} source category {suffix}",
        ontology_type="category",
        domain_id=domain_id,
    )
    source = SourceDocument(
        id=f"src_{suffix}",
        title=f"{domain_id} source",
        object_key=f"{suffix}.md",
        media_type="text/markdown",
        sha256=f"sha_{suffix}",
        domain_id=domain_id,
        category_id=category.id,
    )
    version = SourceDocumentVersion(
        id=f"version_{suffix}", source_document_id=source.id, version_sha256=f"version_sha_{suffix}", status="active"
    )
    element = SourceElement(
        id=f"elem_{suffix}",
        source_version_id=version.id,
        kind="paragraph",
        ordinal=1,
        text=f"{domain_id} architecture evidence.",
    )
    block = IdeaBlock(id=f"block_{suffix}", domain_id=domain_id)
    session.add_all((source, version, element, block))
    session.flush()
    revision = create_idea_block_revision(
        session,
        block,
        statement=f"{domain_id} architecture evidence.",
        normalized_statement=f"{domain_id} architecture evidence",
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
    return element.id, revision.id


def test_resolution_uses_only_external_or_unambiguous_exact_matches(session: Session) -> None:
    element_id, revision_id = _source_revision(session, "commerce", "resolution")
    api = create_entity(
        session, preferred_label="Order API", ontology_type="api", domain_id="commerce", external_id="service:order-api"
    )
    add_alias(
        session,
        entity=api,
        alias="Orders API",
        source_provenance={"kind": "source"},
        actor_id="reviewer",
        reason="source-established alias",
    )

    exact = resolve_mention(
        session,
        text="Orders API",
        proposed_type="api",
        domain_id="commerce",
        source_element_id=element_id,
        idea_block_revision_id=revision_id,
    )
    external = resolve_mention(
        session,
        text="Order interface",
        proposed_type="api",
        domain_id="commerce",
        source_element_id=element_id,
        idea_block_revision_id=revision_id,
        external_id="service:order-api",
    )
    fuzzy = resolve_mention(
        session,
        text="Order Apis",
        proposed_type="api",
        domain_id="commerce",
        source_element_id=element_id,
        idea_block_revision_id=revision_id,
    )

    assert exact.entity is api
    assert exact.matched_by == "exact_alias"
    assert external.entity is api
    assert external.matched_by == "external_id"
    assert fuzzy.entity is None
    assert fuzzy.decision is not None and fuzzy.decision.method == "fuzzy_ranked"
    assert api.id in fuzzy.candidate_entity_ids
    assert session.scalar(select(ReviewTask).where(ReviewTask.target_id == fuzzy.mention.id)) is not None

    model_candidate = resolve_mention(
        session,
        text="Order interface",
        proposed_type="api",
        domain_id="commerce",
        source_element_id=element_id,
        idea_block_revision_id=revision_id,
        supplied_candidate_ids=(api.id,),
        canonical_id=api.id,
    )
    assert model_candidate.entity is None
    assert model_candidate.matched_by == "supplied_canonical_id_review"
    assert model_candidate.candidate_entity_ids == (api.id,)
    assert model_candidate.mention is not None
    assert session.scalar(select(ReviewTask).where(ReviewTask.target_id == model_candidate.mention.id)) is not None

    with pytest.raises(ValueError, match="was not supplied"):
        resolve_mention(
            session,
            text="Order API",
            proposed_type="api",
            domain_id="commerce",
            source_element_id=element_id,
            idea_block_revision_id=revision_id,
            canonical_id=api.id,
        )


def test_unresolved_mentions_reuse_one_open_task_and_preserve_decision_history(session: Session) -> None:
    element_id, revision_id = _source_revision(session, "commerce", "unresolved")
    target = create_entity(session, preferred_label="Order Service", ontology_type="service", domain_id="commerce")

    first = resolve_mention(
        session,
        text="New order service",
        proposed_type="service",
        domain_id="commerce",
        source_element_id=element_id,
        idea_block_revision_id=revision_id,
    )
    second = resolve_mention(
        session,
        text="New order service",
        proposed_type="service",
        domain_id="commerce",
        source_element_id=element_id,
        idea_block_revision_id=revision_id,
    )
    assert first.mention is not None and second.mention is not None
    assert first.mention.id == second.mention.id
    assert session.scalars(select(ReviewTask).where(ReviewTask.target_id == first.mention.id)).all()
    assert len(session.scalars(select(ReviewTask).where(ReviewTask.target_id == first.mention.id)).all()) == 1

    approved = approve_mention_resolution(
        session,
        mention=first.mention,
        entity=target,
        actor_id="reviewer",
        reason="source context confirms the canonical service",
    )
    decisions = session.scalars(
        select(EntityResolutionDecision)
        .where(EntityResolutionDecision.mention_id == first.mention.id)
        .order_by(EntityResolutionDecision.sequence)
    ).all()

    assert approved.is_current
    assert [decision.is_current for decision in decisions] == [False, False, True]
    assert decisions[-1].method == "human_review"


def test_preferred_label_remains_a_strict_alias_after_an_entity_update(session: Session) -> None:
    element_id, revision_id = _source_revision(session, "commerce", "renamed_entity")
    entity = create_entity(session, preferred_label="Order API", ontology_type="api", domain_id="commerce")

    update_entity(
        session,
        entity=entity,
        preferred_label="Orders API",
        actor_id="reviewer",
        reason="canonical product name changed",
    )
    resolved = resolve_mention(
        session,
        text="Orders API",
        proposed_type="api",
        domain_id="commerce",
        source_element_id=element_id,
        idea_block_revision_id=revision_id,
    )
    preferred_alias = session.scalar(
        select(EntityAlias).where(
            EntityAlias.entity_id == entity.id,
            EntityAlias.normalized_alias == "orders api",
            EntityAlias.lifecycle_status == "active",
        )
    )

    assert resolved.entity is entity
    assert resolved.matched_by == "exact_alias"
    assert preferred_alias is not None
    with pytest.raises(ValueError, match="preferred-label alias"):
        deprecate_alias(
            session, alias=preferred_alias, actor_id="reviewer", reason="must preserve canonical label resolution"
        )


def test_cross_type_homonyms_are_review_only_candidates(session: Session) -> None:
    element_id, revision_id = _source_revision(session, "commerce", "homonym")
    datastore_cluster = create_entity(
        session, preferred_label="Cluster", ontology_type="datastore", domain_id="commerce"
    )
    platform_cluster = create_entity(session, preferred_label="Cluster", ontology_type="platform", domain_id="commerce")

    result = resolve_mention(
        session,
        text="Cluster",
        proposed_type="concept",
        domain_id="commerce",
        source_element_id=element_id,
        idea_block_revision_id=revision_id,
    )

    assert result.entity is None
    assert result.matched_by == "ambiguous_homonym"
    assert set(result.candidate_entity_ids) == {datastore_cluster.id, platform_cluster.id}
    assert result.decision is not None and result.decision.method == "ambiguous_homonym"
    assert result.mention is not None
    assert session.scalar(select(ReviewTask).where(ReviewTask.target_id == result.mention.id)) is not None


def test_vocabulary_grounding_fixture_keeps_unknowns_as_mentions(session: Session) -> None:
    fixture = next(item for item in load_gold_corpus().fixtures if item.fixture_id == "vocabulary_grounding_suite")
    outcomes = {str(item["mention"]): item for item in fixture.entity_outcomes}
    expected_ids = {
        name: str(outcome["canonical_id"]) for name, outcome in outcomes.items() if "canonical_id" in outcome
    }
    entities = {
        "postgresql": catalog_entity(
            entity_id=expected_ids["PostgreSQL"],
            preferred_label="PostgreSQL",
            ontology_type="datastore",
            domain_id="platform",
        ),
        "kubernetes": catalog_entity(
            entity_id=expected_ids["K8s"], preferred_label="Kubernetes", ontology_type="platform", domain_id="platform"
        ),
        "postgresql_cluster": catalog_entity(
            entity_id=str(outcomes["Cluster"]["candidates"][0]),
            preferred_label="Cluster",
            ontology_type="datastore",
            domain_id="platform",
        ),
        "kubernetes_cluster": catalog_entity(
            entity_id=str(outcomes["Cluster"]["candidates"][1]),
            preferred_label="Cluster",
            ontology_type="platform",
            domain_id="platform",
        ),
    }
    session.add_all(entities.values())
    session.flush()
    for entity in entities.values():
        add_alias(
            session,
            entity=entity,
            alias=entity.preferred_label,
            source_provenance={"kind": "gold_fixture"},
            actor_id="reviewer",
            reason="gold fixture canonical label",
            schedule=False,
        )
    add_alias(
        session,
        entity=entities["kubernetes"],
        alias="K8s",
        source_provenance={"kind": "gold_fixture"},
        actor_id="reviewer",
        reason="gold fixture alias",
        schedule=False,
    )
    element_id, revision_id = _source_revision(session, "platform", "gold_grounding")

    canonical = resolve_mention(
        session,
        text="PostgreSQL",
        proposed_type="datastore",
        domain_id="platform",
        source_element_id=element_id,
        idea_block_revision_id=revision_id,
    )
    alias = resolve_mention(
        session,
        text="K8s",
        proposed_type="platform",
        domain_id="platform",
        source_element_id=element_id,
        idea_block_revision_id=revision_id,
    )
    homonym = resolve_mention(
        session,
        text="Cluster",
        proposed_type="concept",
        domain_id="platform",
        source_element_id=element_id,
        idea_block_revision_id=revision_id,
    )
    entity_count_before_unknown = len(session.scalars(select(Entity)).all())
    unknown = resolve_mention(
        session,
        text="SuperGraphMeshV3",
        proposed_type="concept",
        domain_id="platform",
        source_element_id=element_id,
        idea_block_revision_id=revision_id,
    )

    assert canonical.entity is not None and canonical.entity.id == expected_ids["PostgreSQL"]
    assert alias.entity is not None and alias.entity.id == expected_ids["K8s"]
    assert homonym.entity is None
    assert set(homonym.candidate_entity_ids) == set(outcomes["Cluster"]["candidates"])
    assert unknown.entity is None and unknown.mention is not None
    assert len(session.scalars(select(Entity)).all()) == entity_count_before_unknown
    assert outcomes["SuperGraphMeshV3"]["review_task_generated"] is True
    assert session.scalar(select(ReviewTask).where(ReviewTask.target_id == unknown.mention.id)) is not None


def test_alias_changes_reresolve_only_affected_mentions_and_preserve_human_decisions(session: Session) -> None:
    commerce_element, commerce_revision = _source_revision(session, "commerce", "reresolve_commerce")
    billing_element, billing_revision = _source_revision(session, "billing", "reresolve_billing")
    service = create_entity(
        session, preferred_label="Order Service Canonical", ontology_type="service", domain_id="commerce"
    )
    unresolved = resolve_mention(
        session,
        text="Order Service",
        proposed_type="service",
        domain_id="commerce",
        source_element_id=commerce_element,
        idea_block_revision_id=commerce_revision,
    )
    billing = resolve_mention(
        session,
        text="Billing Service",
        proposed_type="service",
        domain_id="billing",
        source_element_id=billing_element,
        idea_block_revision_id=billing_revision,
    )
    assert unresolved.mention is not None and billing.mention is not None

    add_alias(
        session,
        entity=service,
        alias="Order Service",
        source_provenance={"kind": "source"},
        actor_id="reviewer",
        reason="approved alias",
    )
    job_id = next(iter(session.info["entity_reresolution_job_ids"]))
    job = session.get(EntityReresolutionJob, job_id)
    assert job is not None
    result = reresolve_affected_entities(session, job=job)

    current = session.scalar(
        select(EntityResolutionDecision).where(
            EntityResolutionDecision.mention_id == unresolved.mention.id, EntityResolutionDecision.is_current.is_(True)
        )
    )
    billing_current = session.scalar(
        select(EntityResolutionDecision).where(
            EntityResolutionDecision.mention_id == billing.mention.id, EntityResolutionDecision.is_current.is_(True)
        )
    )
    assert result["mentions"] == 1
    assert current is not None and current.entity_id == service.id
    assert billing_current is not None and billing_current.entity_id is None

    protected = approve_mention_resolution(
        session, mention=unresolved.mention, entity=service, actor_id="reviewer", reason="human confirmation"
    )
    protected_job = schedule_affected_reresolution(
        session, domain_id="commerce", entity_ids=(service.id,), normalized_aliases=("order service updated",)
    )
    reresolve_affected_entities(session, job=protected_job)
    refreshed = session.get(EntityResolutionDecision, protected.id)

    assert refreshed is not None and refreshed.is_current
    assert job.state == "completed"
    revision = session.get(IdeaBlockRevision, commerce_revision)
    assert revision is not None and revision.ontology_version == ONTOLOGY_VERSION


def test_merge_moves_links_without_erasing_resolution_history(session: Session) -> None:
    element_id, revision_id = _source_revision(session, "commerce", "merge")
    source = create_entity(
        session, preferred_label="Legacy Order Service", ontology_type="service", domain_id="commerce"
    )
    survivor = create_entity(
        session,
        preferred_label="Order Service",
        ontology_type="service",
        domain_id="commerce",
        external_id="service:order",
    )
    resolved = resolve_mention(
        session,
        text="Legacy Order Service",
        proposed_type="service",
        domain_id="commerce",
        source_element_id=element_id,
        idea_block_revision_id=revision_id,
    )
    assert resolved.mention is not None
    upsert_entity_relationship(session, source=source, target=survivor, relation_type="depends_on")

    merge_entities(session, source=source, survivor=survivor, actor_id="reviewer", reason="duplicate canonical service")
    history = session.scalars(
        select(EntityResolutionDecision)
        .where(EntityResolutionDecision.mention_id == resolved.mention.id)
        .order_by(EntityResolutionDecision.sequence)
    ).all()

    assert source.lifecycle_status == "superseded"
    assert (
        session.get(
            IdeaBlockEntity, {"idea_block_id": "block_merge", "entity_id": source.id, "role": "resolved_mention"}
        )
        is None
    )
    assert (
        session.get(
            IdeaBlockEntity, {"idea_block_id": "block_merge", "entity_id": survivor.id, "role": "resolved_mention"}
        )
        is not None
    )
    assert history[-1].entity_id == survivor.id
    assert history[-1].method == "entity_merge"
    assert not history[-2].is_current
    assert session.scalars(select(EntityRelationship)).all() == []
    source_audit = session.scalars(
        select(AuditEvent).where(AuditEvent.target_type == "entity", AuditEvent.target_id == source.id)
    ).all()
    assert any(event.action == "entity_merge" for event in source_audit)

    incompatible = create_entity(
        session,
        preferred_label="Inventory Service",
        ontology_type="service",
        domain_id="commerce",
        external_id="service:inventory",
    )
    with pytest.raises(EntityMergeConflict, match="external_id"):
        merge_entities(session, source=survivor, survivor=incompatible, actor_id="reviewer", reason="must be rejected")


def test_deprecated_alias_removes_stale_automatic_entity_link(session: Session) -> None:
    element_id, revision_id = _source_revision(session, "commerce", "retired_alias")
    service = create_entity(
        session, preferred_label="Order Service Canonical", ontology_type="service", domain_id="commerce"
    )
    alias = add_alias(
        session,
        entity=service,
        alias="Order Service",
        source_provenance={"kind": "source"},
        actor_id="reviewer",
        reason="source-established alias",
    )
    resolved = resolve_mention(
        session,
        text="Order Service",
        proposed_type="service",
        domain_id="commerce",
        source_element_id=element_id,
        idea_block_revision_id=revision_id,
    )
    assert resolved.mention is not None
    first_job_id = next(iter(session.info["entity_reresolution_job_ids"]))
    first_job = session.get(EntityReresolutionJob, first_job_id)
    assert first_job is not None
    reresolve_affected_entities(session, job=first_job)
    assert process_search_materialization_jobs(session) == 1
    initial_revision = session.get(IdeaBlockRevision, revision_id)
    assert initial_revision is not None
    initial_semantic_key = initial_revision.semantic_key
    assert (
        session.get(
            IdeaBlockEntity,
            {"idea_block_id": "block_retired_alias", "entity_id": service.id, "role": "resolved_mention"},
        )
        is not None
    )

    before_job_ids = set(session.info["entity_reresolution_job_ids"])
    deprecate_alias(session, alias=alias, actor_id="reviewer", reason="source label retired")
    new_job_ids = set(session.info["entity_reresolution_job_ids"]) - before_job_ids
    assert len(new_job_ids) == 1
    rereresolution_job = session.get(EntityReresolutionJob, new_job_ids.pop())
    assert rereresolution_job is not None
    reresolve_affected_entities(session, job=rereresolution_job)

    current = session.scalar(
        select(EntityResolutionDecision).where(
            EntityResolutionDecision.mention_id == resolved.mention.id, EntityResolutionDecision.is_current.is_(True)
        )
    )
    refreshed_revision = session.get(IdeaBlockRevision, revision_id)
    assert current is not None and current.entity_id is None
    assert refreshed_revision is not None and refreshed_revision.semantic_key != initial_semantic_key
    assert (
        session.scalar(
            select(SearchMaterializationJob.idea_block_revision_id).where(
                SearchMaterializationJob.idea_block_revision_id == revision_id,
                SearchMaterializationJob.status == "queued",
            )
        )
        == revision_id
    )
    assert (
        session.get(
            IdeaBlockEntity,
            {"idea_block_id": "block_retired_alias", "entity_id": service.id, "role": "resolved_mention"},
        )
        is None
    )
