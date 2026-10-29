"""Canonical entity catalog, controlled vocabulary, and strict resolution."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from uuid import uuid4

from sqlalchemy import func, or_, select, true
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from margin_db.audit import write_audit_event
from margin_db.entity_models import (
    ENTITY_ALIAS_LIFECYCLE_STATES,
    ENTITY_LIFECYCLE_STATES,
    RESOLVED_MENTION_LINK_ROLE,
    Entity,
    EntityAlias,
    EntityMention,
    EntityReresolutionJob,
    EntityResolutionAction,
    EntityResolutionDecision,
)
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock, IdeaBlockEntity, IdeaBlockRevision
from margin_db.relationship_models import EntityRelationship, RelationshipProvenance
from margin_db.review_models import ReviewTask
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement
from margin_db.user_models import User
from margin_schema.ids import entity_id, relationship_id
from margin_schema.ontology import (
    ONTOLOGY_VERSION,
    canonical_relationship_endpoints,
    normalize_entity_kind,
    relationship_definition,
    validate_relationship_endpoints,
)
from margin_schema.text_normalization import normalize_entity_name

ENTITY_RESOLUTION_TASK_TYPE = "entity_resolution_review"
_OPEN_REVIEW_STATES = ("open", "assigned", "needs_evidence")
_UNSET = object()


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    entity: Entity | None
    mention: EntityMention | None
    decision: EntityResolutionDecision | None
    matched_by: str
    candidate_entity_ids: tuple[str, ...]

    @property
    def needs_review(self) -> bool:
        return self.entity is None


@dataclass(frozen=True, slots=True)
class EntityFilterResolution:
    """Read-only Ask/Search entity resolution with explicit ambiguity."""

    entity_id: str | None
    matched_by: str | None
    candidates: tuple[dict[str, str], ...]

    @property
    def is_ambiguous(self) -> bool:
        return self.entity_id is None and len(self.candidates) > 1


class EntityMergeConflict(ValueError):
    """A merge blocker with the exact canonical fields responsible."""

    def __init__(self, blocking_fields: Sequence[str]) -> None:
        self.blocking_fields = tuple(blocking_fields)
        super().__init__("entity merge is blocked by: " + ", ".join(self.blocking_fields))


def create_entity(
    session: Session,
    *,
    preferred_label: str,
    ontology_type: str,
    domain_id: str | None,
    locale: str = "en",
    definition: str | None = None,
    definition_idea_block_id: str | None = None,
    external_id: str | None = None,
    lifecycle_status: str = "active",
    owner_user_id: str | None = None,
    steward_user_id: str | None = None,
    metadata: dict[str, object] | None = None,
    actor_id: str | None = None,
    reason: str = "entity created",
) -> Entity:
    normalized_label, normalized_type, normalized_locale = _validate_entity_input(
        preferred_label=preferred_label, ontology_type=ontology_type, locale=locale, lifecycle_status=lifecycle_status
    )
    _assert_definition_evidence(session, definition=definition, idea_block_id=definition_idea_block_id)
    _assert_unique_external_id(session, external_id=external_id)
    _assert_user_reference(session, user_id=owner_user_id, field_name="owner_user_id")
    _assert_user_reference(session, user_id=steward_user_id, field_name="steward_user_id")
    _assert_unique_canonical_label(
        session,
        normalized_label=normalized_label,
        ontology_type=normalized_type,
        domain_id=domain_id,
        locale=normalized_locale,
    )
    record = Entity(
        id=entity_id(
            {
                "normalized_label": normalized_label,
                "domain_id": domain_id,
                "ontology_type": normalized_type,
                "external_id": external_id,
            }
        ),
        preferred_label=preferred_label.strip(),
        normalized_label=normalized_label,
        definition=definition.strip() if definition else None,
        definition_idea_block_id=definition_idea_block_id,
        ontology_type=normalized_type,
        domain_id=domain_id,
        locale=normalized_locale,
        external_id=external_id.strip() if external_id else None,
        lifecycle_status=lifecycle_status,
        ontology_version=ONTOLOGY_VERSION,
        owner_user_id=owner_user_id,
        steward_user_id=steward_user_id,
        metadata_=dict(metadata or {}),
    )
    session.add(record)
    session.flush()
    add_alias(
        session,
        entity=record,
        alias=record.preferred_label,
        source_provenance={"kind": "preferred_label"},
        actor_id=actor_id,
        reason="canonical label",
        schedule=False,
    )
    _record_action(session, "create", record.id, None, actor_id, reason)
    return record


def update_entity(
    session: Session,
    *,
    entity: Entity,
    preferred_label: str | object = _UNSET,
    ontology_type: str | object = _UNSET,
    locale: str | object = _UNSET,
    definition: str | None | object = _UNSET,
    definition_idea_block_id: str | None | object = _UNSET,
    external_id: str | None | object = _UNSET,
    lifecycle_status: str | object = _UNSET,
    owner_user_id: str | None | object = _UNSET,
    steward_user_id: str | None | object = _UNSET,
    actor_id: str | None = None,
    reason: str = "entity updated",
) -> Entity:
    if entity.lifecycle_status not in {"draft", "active"}:
        raise ValueError(f"cannot update an Entity in {entity.lifecycle_status} lifecycle state")
    before_normalized_label = entity.normalized_label
    before = _entity_snapshot(entity)
    if external_id is not _UNSET and external_id != entity.external_id:
        raise ValueError("external_id is immutable after entity creation")
    proposed_label = preferred_label.strip() if isinstance(preferred_label, str) else entity.preferred_label
    proposed_type = ontology_type if isinstance(ontology_type, str) else entity.ontology_type
    proposed_locale = locale if isinstance(locale, str) else entity.locale
    proposed_status = lifecycle_status if isinstance(lifecycle_status, str) else entity.lifecycle_status
    normalized_label, normalized_type, normalized_locale = _validate_entity_input(
        preferred_label=proposed_label,
        ontology_type=proposed_type,
        locale=proposed_locale,
        lifecycle_status=proposed_status,
    )
    proposed_definition = (
        definition if isinstance(definition, str) else None if definition is None else entity.definition
    )
    proposed_definition_link = (
        definition_idea_block_id
        if isinstance(definition_idea_block_id, str)
        else None if definition_idea_block_id is None else entity.definition_idea_block_id
    )
    _assert_definition_evidence(session, definition=proposed_definition, idea_block_id=proposed_definition_link)
    _assert_unique_canonical_label(
        session,
        normalized_label=normalized_label,
        ontology_type=normalized_type,
        domain_id=entity.domain_id,
        locale=normalized_locale,
        excluded_entity_id=entity.id,
    )
    _assert_prospective_alias_scopes(
        session,
        entity=entity,
        normalized_label=normalized_label,
        ontology_type=normalized_type,
        locale=normalized_locale,
    )
    if normalized_type != entity.ontology_type:
        _validate_entity_relationship_types(session, entity=entity, proposed_type=normalized_type)
    entity.preferred_label = proposed_label
    entity.normalized_label = normalized_label
    entity.ontology_type = normalized_type
    entity.locale = normalized_locale
    entity.lifecycle_status = proposed_status
    entity.ontology_version = ONTOLOGY_VERSION
    if definition is not _UNSET:
        entity.definition = (definition.strip() or None) if isinstance(definition, str) else None
    if definition_idea_block_id is not _UNSET:
        entity.definition_idea_block_id = definition_idea_block_id
    elif entity.definition is None:
        entity.definition_idea_block_id = None
    if owner_user_id is not _UNSET:
        owner_user_id = _optional_user_id(owner_user_id, field_name="owner_user_id")
        _assert_user_reference(session, user_id=owner_user_id, field_name="owner_user_id")
        entity.owner_user_id = owner_user_id
    if steward_user_id is not _UNSET:
        steward_user_id = _optional_user_id(steward_user_id, field_name="steward_user_id")
        _assert_user_reference(session, user_id=steward_user_id, field_name="steward_user_id")
        entity.steward_user_id = steward_user_id
    for alias in session.scalars(select(EntityAlias).where(EntityAlias.entity_id == entity.id)).all():
        alias.ontology_type = normalized_type
        alias.locale = normalized_locale
    _ensure_preferred_label_alias(session, entity=entity)
    _touch_catalog_version(entity)
    _record_action(
        session, "update", entity.id, None, actor_id, reason, {"before": before, "after": _entity_snapshot(entity)}
    )
    session.flush()
    schedule_affected_reresolution(
        session,
        domain_id=entity.domain_id,
        entity_ids=(entity.id,),
        normalized_aliases=(before_normalized_label, entity.normalized_label),
    )
    return entity


def add_alias(
    session: Session,
    *,
    entity: Entity,
    alias: str,
    source_provenance: dict[str, object] | None,
    actor_id: str | None,
    reason: str,
    lifecycle_status: str = "active",
    schedule: bool = True,
) -> EntityAlias:
    if entity.lifecycle_status not in {"draft", "active"}:
        raise ValueError(f"cannot add an alias to an Entity in {entity.lifecycle_status} lifecycle state")
    normalized_alias = normalize_entity_name(alias)
    if not normalized_alias:
        raise ValueError("entity alias must contain letters or numbers")
    if lifecycle_status not in ENTITY_ALIAS_LIFECYCLE_STATES:
        raise ValueError("entity alias lifecycle_status is invalid")
    _assert_unique_alias(
        session,
        normalized_alias=normalized_alias,
        ontology_type=entity.ontology_type,
        domain_id=entity.domain_id,
        locale=entity.locale,
        excluded_entity_id=entity.id,
    )
    existing = session.scalar(
        select(EntityAlias).where(
            EntityAlias.entity_id == entity.id,
            EntityAlias.normalized_alias == normalized_alias,
            EntityAlias.locale == entity.locale,
        )
    )
    if existing is not None:
        if existing.lifecycle_status == "active":
            return existing
        existing.lifecycle_status = lifecycle_status
        existing.alias = alias.strip()
        existing.source_provenance = dict(source_provenance or {})
        record = existing
    else:
        record = EntityAlias(
            id=_alias_id(entity.id, normalized_alias, entity.locale),
            entity_id=entity.id,
            alias=alias.strip(),
            normalized_alias=normalized_alias,
            locale=entity.locale,
            domain_id=entity.domain_id,
            ontology_type=entity.ontology_type,
            lifecycle_status=lifecycle_status,
            source_provenance=dict(source_provenance or {}),
        )
        session.add(record)
    _touch_catalog_version(entity)
    _record_action(session, "alias_added", entity.id, None, actor_id, reason, {"alias": record.alias})
    if schedule:
        session.flush()
        schedule_affected_reresolution(
            session, domain_id=entity.domain_id, entity_ids=(entity.id,), normalized_aliases=(normalized_alias,)
        )
    return record


def deprecate_alias(session: Session, *, alias: EntityAlias, actor_id: str | None, reason: str) -> EntityAlias:
    if alias.lifecycle_status != "active":
        return alias
    entity = session.get(Entity, alias.entity_id)
    if entity is not None and entity.lifecycle_status not in {"draft", "active"}:
        raise ValueError(f"cannot deprecate an alias for an Entity in {entity.lifecycle_status} lifecycle state")
    if entity is not None and alias.normalized_alias == entity.normalized_label:
        raise ValueError("cannot deprecate the preferred-label alias; update the preferred label first")
    alias.lifecycle_status = "deprecated"
    _record_action(session, "alias_deprecated", alias.entity_id, None, actor_id, reason, {"alias": alias.alias})
    if entity is not None:
        _touch_catalog_version(entity)
        session.flush()
        schedule_affected_reresolution(
            session, domain_id=entity.domain_id, entity_ids=(entity.id,), normalized_aliases=(alias.normalized_alias,)
        )
    return alias


def resolve_mention(
    session: Session,
    *,
    text: str,
    proposed_type: str,
    domain_id: str,
    source_element_id: str,
    idea_block_revision_id: str,
    external_id: str | None = None,
    context: dict[str, object] | None = None,
    supplied_candidate_ids: Iterable[str] = (),
    canonical_id: str | None = None,
) -> ResolutionResult:
    normalized_text = normalize_entity_name(text)
    normalized_type = normalize_entity_kind(proposed_type)
    if not normalized_text or normalized_type is None:
        raise ValueError("entity mention requires text and an active ontology type")
    candidate_ids = tuple(dict.fromkeys(value for value in supplied_candidate_ids if value))
    match, matched_by, ranked_ids = _strict_match(
        session,
        normalized_text=normalized_text,
        ontology_type=normalized_type,
        domain_id=domain_id,
        external_id=external_id,
        candidate_ids=candidate_ids,
        canonical_id=canonical_id,
    )
    review_candidate_ids = tuple(dict.fromkeys((*ranked_ids, *candidate_ids)))
    mention = _upsert_mention(
        session,
        text=text,
        normalized_text=normalized_text,
        proposed_type=normalized_type,
        domain_id=domain_id,
        source_element_id=source_element_id,
        idea_block_revision_id=idea_block_revision_id,
        external_id=external_id,
        context=context,
        candidate_ids=review_candidate_ids,
    )
    if match is not None:
        decision = _record_resolution_decision(
            session,
            mention=mention,
            entity=match,
            outcome="resolved",
            method=matched_by,
            confidence=1.0,
            candidate_ids=mention.candidate_entity_ids,
            rationale="strict canonical entity match",
            actor_id=None,
        )
        _link_resolution_to_idea_block(session, mention=mention, entity=match)
        return ResolutionResult(match, mention, decision, matched_by, tuple(mention.candidate_entity_ids))
    decision = _record_resolution_decision(
        session,
        mention=mention,
        entity=None,
        outcome="unresolved" if not ranked_ids else "ambiguous",
        method=matched_by,
        confidence=0.0,
        candidate_ids=mention.candidate_entity_ids,
        rationale="fuzzy and contextual candidates require human review",
        actor_id=None,
    )
    _ensure_review_task(session, mention=mention, candidate_ids=mention.candidate_entity_ids)
    return ResolutionResult(None, mention, decision, decision.method, tuple(mention.candidate_entity_ids))


def match_existing_entity(
    session: Session,
    *,
    text: str,
    ontology_type: str,
    domain_id: str,
    external_id: str | None = None,
    supplied_candidate_ids: Iterable[str] = (),
    canonical_id: str | None = None,
) -> ResolutionResult:
    normalized_text = normalize_entity_name(text)
    normalized_type = normalize_entity_kind(ontology_type)
    if not normalized_text or normalized_type is None:
        raise ValueError("entity match requires text and an active ontology type")
    match, matched_by, ranked_ids = _strict_match(
        session,
        normalized_text=normalized_text,
        ontology_type=normalized_type,
        domain_id=domain_id,
        external_id=external_id,
        candidate_ids=tuple(dict.fromkeys(value for value in supplied_candidate_ids if value)),
        canonical_id=canonical_id,
    )
    return ResolutionResult(match, None, None, matched_by, ranked_ids)


def resolve_entity_filter(
    session: Session, *, text: str, domain_ids: Iterable[str] = (), ontology_types: Iterable[str] = ()
) -> EntityFilterResolution:
    """Resolve one search term without choosing between conflicting active aliases."""

    normalized_text = normalize_entity_name(text)
    if not normalized_text:
        return EntityFilterResolution(None, None, ())
    domains = tuple(dict.fromkeys(domain_ids))
    types = tuple(dict.fromkeys(normalize_entity_kind(value) for value in ontology_types))
    if any(value is None for value in types):
        raise ValueError("entity filter includes an unknown ontology type")
    query = (
        select(Entity, EntityAlias)
        .outerjoin(EntityAlias, (EntityAlias.entity_id == Entity.id) & (EntityAlias.lifecycle_status == "active"))
        .where(Entity.lifecycle_status == "active")
        .where((Entity.normalized_label == normalized_text) | (EntityAlias.normalized_alias == normalized_text))
    )
    if domains:
        query = query.where((Entity.domain_id.in_(domains)) | (Entity.domain_id.is_(None)))
    normalized_types = tuple(value for value in types if value is not None)
    if normalized_types:
        query = query.where(Entity.ontology_type.in_(normalized_types))
    rows = session.execute(query.order_by(Entity.id)).all()
    entities = {entity.id: entity for entity, _alias in rows}
    candidates = tuple(
        {
            "id": entity.id,
            "label": entity.preferred_label,
            "type": entity.ontology_type,
            "domain_id": entity.domain_id or "",
        }
        for entity in entities.values()
    )
    if len(candidates) != 1:
        return EntityFilterResolution(None, None, candidates)
    entity = next(iter(entities.values()))
    matched_by = "canonical" if entity.normalized_label == normalized_text else "alias"
    return EntityFilterResolution(entity.id, matched_by, candidates)


def approve_mention_resolution(
    session: Session, *, mention: EntityMention, entity: Entity, actor_id: str | None, reason: str
) -> EntityResolutionDecision:
    if not _is_active_in_scope(entity, domain_id=mention.domain_id, ontology_types=(mention.proposed_type,)):
        raise ValueError("resolution target must be active and match the mention domain and ontology type")
    _validate_pending_relationship_endpoints(session, mention=mention, entity=entity)
    decision = _record_resolution_decision(
        session,
        mention=mention,
        entity=entity,
        outcome="resolved",
        method="human_review",
        confidence=1.0,
        candidate_ids=mention.candidate_entity_ids,
        rationale=reason,
        actor_id=actor_id,
    )
    _link_resolution_to_idea_block(session, mention=mention, entity=entity)
    _close_review_task(session, mention.id, reason)
    _record_action(session, "mention_approved", entity.id, None, actor_id, reason, {"mention_id": mention.id})
    return decision


def schedule_affected_reresolution(
    session: Session, *, domain_id: str | None, entity_ids: Iterable[str], normalized_aliases: Iterable[str]
) -> EntityReresolutionJob:
    ids = tuple(sorted({value for value in entity_ids if value}))
    aliases = tuple(sorted({value for value in normalized_aliases if value}))
    versions = {
        record.id: record.catalog_version for record in session.scalars(select(Entity).where(Entity.id.in_(ids))).all()
    }
    payload = {
        "domain_id": domain_id,
        "entity_ids": ids,
        "entity_versions": versions,
        "aliases": aliases,
        "ontology_version": ONTOLOGY_VERSION,
    }
    job_id = entity_id({"normalized_label": "entity reresolution", "job": payload}).replace("ent_", "entity_job_", 1)
    existing = session.scalar(select(EntityReresolutionJob).where(EntityReresolutionJob.deduplication_key == job_id))
    if existing is not None:
        session.info.setdefault("entity_reresolution_job_ids", set()).add(existing.id)
        _queue_affected_search_materialization(session, entity_ids=ids)
        return existing
    job = EntityReresolutionJob(
        id=job_id,
        deduplication_key=job_id,
        domain_id=domain_id,
        entity_ids=list(ids),
        entity_versions=versions,
        normalized_aliases=list(aliases),
        ontology_version=ONTOLOGY_VERSION,
    )
    session.add(job)
    session.info.setdefault("entity_reresolution_job_ids", set()).add(job.id)
    _queue_affected_search_materialization(session, entity_ids=ids)
    return job


def _queue_affected_search_materialization(session: Session, *, entity_ids: Iterable[str]) -> None:
    from margin_db.search_materialization import enqueue_search_materialization_for_entities

    enqueue_search_materialization_for_entities(session, entity_ids, reason="approved_entity_label_or_alias_changed")


def reresolve_affected_entities(session: Session, *, job: EntityReresolutionJob) -> dict[str, int]:
    from margin_db.entity_catalog_jobs import reresolve_affected_entities as run_affected_reresolution

    return run_affected_reresolution(session, job=job)


def _strict_match(
    session: Session,
    *,
    normalized_text: str,
    ontology_type: str,
    domain_id: str,
    external_id: str | None,
    candidate_ids: tuple[str, ...],
    canonical_id: str | None,
) -> tuple[Entity | None, str, tuple[str, ...]]:
    scope = (
        Entity.lifecycle_status == "active",
        Entity.ontology_type == ontology_type,
        _domain_scope(Entity.domain_id, domain_id),
    )
    selected_candidate_id: str | None = None
    if canonical_id:
        if canonical_id not in candidate_ids:
            raise ValueError("canonical_id was not supplied in this extraction window's candidate set")
        canonical = session.get(Entity, canonical_id)
        if canonical is None or not _is_active_in_scope(canonical, domain_id, (ontology_type,)):
            raise ValueError("canonical_id is inactive or incompatible with the mention ontology type")
        selected_candidate_id = canonical_id
    if external_id:
        matches = list(session.scalars(select(Entity).where(*scope, Entity.external_id == external_id)).all())
        if len(matches) == 1:
            return matches[0], "external_id", ()
        if len(matches) > 1:
            return None, "ambiguous_external_id", tuple(sorted(item.id for item in matches))
    aliases = list(
        session.scalars(
            select(EntityAlias)
            .where(
                EntityAlias.lifecycle_status == "active",
                EntityAlias.ontology_type == ontology_type,
                EntityAlias.normalized_alias == normalized_text,
                _domain_scope(EntityAlias.domain_id, domain_id),
            )
            .order_by(EntityAlias.entity_id)
        ).all()
    )
    matches = [session.get(Entity, alias.entity_id) for alias in aliases]
    matches = [item for item in matches if item is not None and _is_active_in_scope(item, domain_id, (ontology_type,))]
    unique_matches = {item.id: item for item in matches}
    if len(unique_matches) == 1:
        return next(iter(unique_matches.values())), "exact_alias", ()
    if len(unique_matches) > 1:
        return None, "ambiguous_alias", tuple(sorted(unique_matches))
    if selected_candidate_id is not None:
        return None, "supplied_canonical_id_review", (selected_candidate_id,)
    homonym_ids = _cross_type_homonym_ids(
        session, normalized_text=normalized_text, domain_id=domain_id, excluded_ontology_type=ontology_type
    )
    if len(homonym_ids) > 1:
        return None, "ambiguous_homonym", homonym_ids
    candidates = list(session.scalars(select(Entity).where(*scope).order_by(Entity.id)).all())
    if candidate_ids:
        allowed = set(candidate_ids)
        candidates = [item for item in candidates if item.id in allowed]
    scores = sorted(
        (
            (SequenceMatcher(None, normalized_text, item.normalized_label).ratio(), item.id)
            for item in candidates
            if _candidate_similarity(normalized_text, item.normalized_label) >= 0.6
        ),
        key=lambda item: (-item[0], item[1]),
    )
    return None, "fuzzy_ranked", tuple(item_id for _score, item_id in scores[:5])


def _cross_type_homonym_ids(
    session: Session, *, normalized_text: str, domain_id: str, excluded_ontology_type: str
) -> tuple[str, ...]:
    """Return same-label entities that require human type disambiguation."""

    canonical_ids = session.scalars(
        select(Entity.id).where(
            Entity.lifecycle_status == "active",
            Entity.normalized_label == normalized_text,
            Entity.ontology_type != excluded_ontology_type,
            _domain_scope(Entity.domain_id, domain_id),
        )
    ).all()
    alias_ids = session.scalars(
        select(Entity.id)
        .join(EntityAlias, EntityAlias.entity_id == Entity.id)
        .where(
            Entity.lifecycle_status == "active",
            EntityAlias.lifecycle_status == "active",
            EntityAlias.normalized_alias == normalized_text,
            Entity.ontology_type != excluded_ontology_type,
            _domain_scope(Entity.domain_id, domain_id),
        )
    ).all()
    return tuple(sorted(set(canonical_ids) | set(alias_ids)))


def _upsert_mention(
    session: Session,
    *,
    text: str,
    normalized_text: str,
    proposed_type: str,
    domain_id: str,
    source_element_id: str,
    idea_block_revision_id: str,
    external_id: str | None,
    context: dict[str, object] | None,
    candidate_ids: tuple[str, ...],
) -> EntityMention:
    mention_id = entity_id(
        {
            "normalized_label": normalized_text,
            "idea_block_revision_id": idea_block_revision_id,
            "source_element_id": source_element_id,
            "proposed_type": proposed_type,
        }
    ).replace("ent_", "mention_", 1)
    mention = session.get(EntityMention, mention_id)
    if mention is None:
        mention = EntityMention(
            id=mention_id,
            idea_block_revision_id=idea_block_revision_id,
            source_element_id=source_element_id,
            domain_id=domain_id,
            text=text.strip(),
            normalized_text=normalized_text,
            proposed_type=proposed_type,
            external_id=external_id.strip() if external_id else None,
            context=dict(context or {}),
            candidate_entity_ids=list(candidate_ids),
            ontology_version=ONTOLOGY_VERSION,
        )
        session.add(mention)
    else:
        mention.context = dict(context or mention.context or {})
        mention.candidate_entity_ids = list(candidate_ids)
    return mention


def _record_resolution_decision(
    session: Session,
    *,
    mention: EntityMention,
    entity: Entity | None,
    outcome: str,
    method: str,
    confidence: float,
    candidate_ids: Sequence[str],
    rationale: str,
    actor_id: str | None,
) -> EntityResolutionDecision:
    decision = _current_resolution_decision(session, mention.id)
    if decision is not None and decision.decided_by is not None and actor_id is None:
        return decision
    if decision is not None:
        decision.is_current = False
        session.flush()
    session.flush()
    sequence = (
        session.scalar(
            select(func.count(EntityResolutionDecision.id)).where(EntityResolutionDecision.mention_id == mention.id)
        )
        or 0
    ) + 1
    decision = EntityResolutionDecision(
        id=f"decision_{uuid4().hex}",
        mention_id=mention.id,
        sequence=sequence,
        entity_id=entity.id if entity else None,
        outcome=outcome,
        confidence=confidence,
        candidate_entity_ids=list(candidate_ids),
        method=method,
        rationale=rationale,
        status="resolved" if entity else "review_required",
        ontology_version=ONTOLOGY_VERSION,
        decided_by=actor_id,
        is_current=True,
        reviewed_at=datetime.now(UTC) if actor_id else None,
    )
    session.add(decision)
    return decision


def _current_resolution_decision(session: Session, mention_id: str) -> EntityResolutionDecision | None:
    return session.scalar(
        select(EntityResolutionDecision)
        .where(EntityResolutionDecision.mention_id == mention_id, EntityResolutionDecision.is_current.is_(True))
        .order_by(EntityResolutionDecision.sequence.desc(), EntityResolutionDecision.id.desc())
        .limit(1)
    )


def _ensure_review_task(session: Session, *, mention: EntityMention, candidate_ids: Sequence[str]) -> ReviewTask:
    task = session.scalar(
        select(ReviewTask).where(
            ReviewTask.task_type == ENTITY_RESOLUTION_TASK_TYPE,
            ReviewTask.target_type == "entity_mention",
            ReviewTask.target_id == mention.id,
            ReviewTask.status.in_(_OPEN_REVIEW_STATES),
        )
    )
    metadata = {"candidate_entity_ids": list(candidate_ids), "ontology_version": ONTOLOGY_VERSION}
    if task is None:
        task = ReviewTask(
            task_type=ENTITY_RESOLUTION_TASK_TYPE,
            target_type="entity_mention",
            target_id=mention.id,
            reason="canonical entity resolution is unresolved or ambiguous",
            recommendation="review required",
            metadata_=metadata,
        )
        session.add(task)
    else:
        task.metadata_ = metadata
    return task


def _close_review_task(session: Session, mention_id: str, reason: str) -> None:
    for task in session.scalars(
        select(ReviewTask).where(
            ReviewTask.task_type == ENTITY_RESOLUTION_TASK_TYPE,
            ReviewTask.target_type == "entity_mention",
            ReviewTask.target_id == mention_id,
            ReviewTask.status.in_(_OPEN_REVIEW_STATES),
        )
    ).all():
        task.status = "closed"
        task.resolution = reason
        task.closed_at = datetime.now(UTC)


def _link_resolution_to_idea_block(session: Session, *, mention: EntityMention, entity: Entity) -> None:
    revision = session.get(IdeaBlockRevision, mention.idea_block_revision_id)
    if revision is None:
        raise ValueError("entity mention references a missing IdeaBlock revision")
    existing = session.get(
        IdeaBlockEntity,
        {"idea_block_id": revision.idea_block_id, "entity_id": entity.id, "role": RESOLVED_MENTION_LINK_ROLE},
    )
    if existing is None:
        session.add(
            IdeaBlockEntity(idea_block_id=revision.idea_block_id, entity_id=entity.id, role=RESOLVED_MENTION_LINK_ROLE)
        )
        block = session.get(IdeaBlock, revision.idea_block_id)
        if block is not None and block.current_revision_id == revision.id:
            _queue_affected_search_materialization(session, entity_ids=(entity.id,))


def _validate_pending_relationship_endpoints(session: Session, *, mention: EntityMention, entity: Entity) -> None:
    revision = session.get(IdeaBlockRevision, mention.idea_block_revision_id)
    if revision is None:
        return
    relationships = session.scalars(
        select(EntityRelationship)
        .join(RelationshipProvenance, RelationshipProvenance.relationship_id == EntityRelationship.id)
        .where(RelationshipProvenance.idea_block_id == revision.idea_block_id)
    ).all()
    for relationship in relationships:
        if relationship.source_entity_id == entity.id or relationship.target_entity_id == entity.id:
            source = session.get(Entity, relationship.source_entity_id)
            target = session.get(Entity, relationship.target_entity_id)
            if source is not None and target is not None:
                validate_relationship_endpoints(relationship.relation_type, source.ontology_type, target.ontology_type)


def _validate_entity_relationship_types(session: Session, *, entity: Entity, proposed_type: str) -> None:
    relationships = session.scalars(
        select(EntityRelationship).where(
            or_(EntityRelationship.source_entity_id == entity.id, EntityRelationship.target_entity_id == entity.id)
        )
    ).all()
    for relationship in relationships:
        source = session.get(Entity, relationship.source_entity_id)
        target = session.get(Entity, relationship.target_entity_id)
        if source is None or target is None:
            continue
        source_type = proposed_type if source.id == entity.id else source.ontology_type
        target_type = proposed_type if target.id == entity.id else target.ontology_type
        validate_relationship_endpoints(relationship.relation_type, source_type, target_type)


def _upsert_catalog_relationship(
    session: Session, *, source: Entity, target: Entity, relation_type: str
) -> EntityRelationship:
    relation_type = relationship_definition(relation_type).name
    if source.id == target.id:
        raise ValueError("Entity relationships require distinct endpoints")
    if source.lifecycle_status != "active" or target.lifecycle_status != "active":
        raise ValueError("Entity relationships require active entity endpoints")
    if source.domain_id != target.domain_id:
        raise ValueError("Entity relationships require endpoints in the same domain")
    validate_relationship_endpoints(relation_type, source.ontology_type, target.ontology_type)
    source_id, target_id = canonical_relationship_endpoints(relation_type, source.id, target.id)
    existing = session.scalar(
        select(EntityRelationship).where(
            EntityRelationship.source_entity_id == source_id,
            EntityRelationship.target_entity_id == target_id,
            EntityRelationship.relation_type == relation_type,
        )
    )
    if existing is not None:
        return existing
    relationship = EntityRelationship(
        id=relationship_id(
            {"source_entity_id": source_id, "target_entity_id": target_id, "relation_type": relation_type}
        ),
        source_entity_id=source_id,
        target_entity_id=target_id,
        relation_type=relation_type,
        ontology_version=ONTOLOGY_VERSION,
        confidence="verified",
        status="verified",
        metadata_={"origin": "vocabulary_dto"},
    )
    session.add(relationship)
    return relationship


def upsert_entity_relationship(
    session: Session, *, source: Entity, target: Entity, relation_type: str
) -> EntityRelationship:
    """Persist one ontology-constrained Entity relationship from a catalog owner."""

    return _upsert_catalog_relationship(session, source=source, target=target, relation_type=relation_type)


def _validate_entity_input(
    *, preferred_label: str, ontology_type: str, locale: str, lifecycle_status: str
) -> tuple[str, str, str]:
    normalized_label = normalize_entity_name(preferred_label)
    normalized_type = normalize_entity_kind(ontology_type)
    normalized_locale = locale.strip().casefold()
    if not normalized_label:
        raise ValueError("preferred_label must contain letters or numbers")
    if normalized_type is None:
        raise ValueError("ontology_type is not in the active ontology")
    if not normalized_locale:
        raise ValueError("locale cannot be blank")
    if lifecycle_status not in ENTITY_LIFECYCLE_STATES:
        raise ValueError("entity lifecycle_status is invalid")
    return normalized_label, normalized_type, normalized_locale


def _assert_definition_evidence(session: Session, *, definition: str | None, idea_block_id: str | None) -> None:
    if not definition:
        return
    if not idea_block_id:
        raise ValueError("an approved definition requires a definition IdeaBlock with source evidence")
    evidence = session.scalar(
        select(ClaimEvidence.id)
        .join(IdeaBlock, IdeaBlock.id == ClaimEvidence.idea_block_id)
        .join(SourceElement, SourceElement.id == ClaimEvidence.source_element_id)
        .join(SourceDocumentVersion, SourceDocumentVersion.id == SourceElement.source_version_id)
        .join(SourceDocument, SourceDocument.id == SourceDocumentVersion.source_document_id)
        .where(
            ClaimEvidence.idea_block_id == idea_block_id,
            SourceDocument.metadata_["ontology_source"].as_boolean().is_(True),
        )
        .limit(1)
    )
    if evidence is None:
        raise ValueError("definition IdeaBlock must contain ClaimEvidence from an ontology_source document")


def _assert_user_reference(session: Session, *, user_id: str | None, field_name: str) -> None:
    if user_id is not None and session.get(User, user_id) is None:
        raise ValueError(f"{field_name} must reference an existing user")


def _optional_user_id(value: object, *, field_name: str) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise TypeError(f"{field_name} must be a string or null")


def _assert_unique_external_id(
    session: Session, *, external_id: str | None, excluded_entity_id: str | None = None
) -> None:
    if not external_id:
        return
    statement = select(Entity.id).where(Entity.external_id == external_id.strip())
    if excluded_entity_id:
        statement = statement.where(Entity.id != excluded_entity_id)
    if session.scalar(statement.limit(1)) is not None:
        raise ValueError("external_id is already assigned to a canonical Entity")


def _assert_unique_canonical_label(
    session: Session,
    *,
    normalized_label: str,
    ontology_type: str,
    domain_id: str | None,
    locale: str,
    excluded_entity_id: str | None = None,
) -> None:
    statement = select(Entity.id).where(
        Entity.normalized_label == normalized_label,
        Entity.ontology_type == ontology_type,
        Entity.locale == locale,
        _overlapping_domain_scope(Entity.domain_id, domain_id),
        Entity.lifecycle_status != "retired",
    )
    if excluded_entity_id:
        statement = statement.where(Entity.id != excluded_entity_id)
    if session.scalar(statement.limit(1)) is not None:
        raise ValueError("canonical entity label already exists in this domain/type/locale scope")


def _assert_unique_alias(
    session: Session,
    *,
    normalized_alias: str,
    ontology_type: str,
    domain_id: str | None,
    locale: str,
    excluded_entity_id: str | None,
) -> None:
    alias_statement = select(EntityAlias.entity_id).where(
        EntityAlias.lifecycle_status == "active",
        EntityAlias.normalized_alias == normalized_alias,
        EntityAlias.ontology_type == ontology_type,
        EntityAlias.locale == locale,
        _overlapping_domain_scope(EntityAlias.domain_id, domain_id),
    )
    if excluded_entity_id:
        alias_statement = alias_statement.where(EntityAlias.entity_id != excluded_entity_id)
    if session.scalar(alias_statement.limit(1)) is not None:
        raise ValueError("entity alias is already assigned within this domain/type/locale scope")
    canonical_statement = select(Entity.id).where(
        Entity.normalized_label == normalized_alias,
        Entity.ontology_type == ontology_type,
        Entity.locale == locale,
        _overlapping_domain_scope(Entity.domain_id, domain_id),
        Entity.lifecycle_status == "active",
    )
    if excluded_entity_id:
        canonical_statement = canonical_statement.where(Entity.id != excluded_entity_id)
    if session.scalar(canonical_statement.limit(1)) is not None:
        raise ValueError("entity alias conflicts with an active canonical label")


def _assert_prospective_alias_scopes(
    session: Session, *, entity: Entity, normalized_label: str, ontology_type: str, locale: str
) -> None:
    aliases = list(session.scalars(select(EntityAlias).where(EntityAlias.entity_id == entity.id)).all())
    alias_values = [alias.normalized_alias for alias in aliases]
    if normalized_label not in alias_values:
        alias_values.append(normalized_label)
    if len(alias_values) != len(set(alias_values)):
        raise ValueError("entity aliases would collide in the requested locale")
    for normalized_alias in set(alias_values):
        _assert_unique_alias(
            session,
            normalized_alias=normalized_alias,
            ontology_type=ontology_type,
            domain_id=entity.domain_id,
            locale=locale,
            excluded_entity_id=entity.id,
        )


def _ensure_preferred_label_alias(session: Session, *, entity: Entity) -> None:
    alias = session.scalar(
        select(EntityAlias).where(
            EntityAlias.entity_id == entity.id,
            EntityAlias.normalized_alias == entity.normalized_label,
            EntityAlias.locale == entity.locale,
        )
    )
    if alias is None:
        alias = EntityAlias(
            id=_alias_id(entity.id, entity.normalized_label, entity.locale),
            entity_id=entity.id,
            alias=entity.preferred_label,
            normalized_alias=entity.normalized_label,
            locale=entity.locale,
            domain_id=entity.domain_id,
            ontology_type=entity.ontology_type,
            lifecycle_status="active",
            source_provenance={"kind": "preferred_label"},
        )
        session.add(alias)
        return
    alias.alias = entity.preferred_label
    alias.domain_id = entity.domain_id
    alias.ontology_type = entity.ontology_type
    alias.lifecycle_status = "active"
    alias.source_provenance = {**(alias.source_provenance or {}), "kind": "preferred_label"}


def _is_active_in_scope(entity: Entity, domain_id: str, ontology_types: Iterable[str]) -> bool:
    allowed_types = set(ontology_types)
    return bool(
        entity.lifecycle_status == "active"
        and (entity.domain_id is None or entity.domain_id == domain_id)
        and (not allowed_types or entity.ontology_type in allowed_types)
    )


def _domain_scope(column: InstrumentedAttribute[str | None], domain_id: str) -> ColumnElement[bool]:
    return or_(column == domain_id, column.is_(None))


def _overlapping_domain_scope(column: InstrumentedAttribute[str | None], domain_id: str | None) -> ColumnElement[bool]:
    if domain_id is None:
        return true()
    return or_(column.is_(None), column == domain_id)


def _normalized_types(values: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(sorted({value for item in values if (value := normalize_entity_kind(item)) is not None}))
    return normalized


def _candidate_similarity(left: str, right: str) -> float:
    token_overlap = set(left.split()).intersection(right.split())
    return max(SequenceMatcher(None, left, right).ratio(), 0.7 if token_overlap else 0.0)


def _alias_id(entity_id_value: str, normalized_alias: str, locale: str) -> str:
    return entity_id(
        {"normalized_label": normalized_alias, "entity_id": entity_id_value, "locale": locale, "kind": "alias"}
    ).replace("ent_", "alias_", 1)


def _entity_snapshot(entity: Entity) -> dict[str, object]:
    return {
        "preferred_label": entity.preferred_label,
        "normalized_label": entity.normalized_label,
        "ontology_type": entity.ontology_type,
        "locale": entity.locale,
        "lifecycle_status": entity.lifecycle_status,
        "definition_idea_block_id": entity.definition_idea_block_id,
    }


def _touch_catalog_version(entity: Entity) -> None:
    entity.catalog_version += 1


def _record_action(
    session: Session,
    action: str,
    source_entity_id: str,
    target_entity_id: str | None,
    actor_id: str | None,
    reason: str,
    metadata: dict[str, object] | None = None,
) -> None:
    record_id = entity_id(
        {
            "normalized_label": source_entity_id,
            "action": action,
            "target_entity_id": target_entity_id,
            "reason": reason,
            "metadata": metadata or {},
        }
    ).replace("ent_", "entity_action_", 1)
    if session.get(EntityResolutionAction, record_id) is None:
        session.add(
            EntityResolutionAction(
                id=record_id,
                action=action,
                source_entity_id=source_entity_id,
                target_entity_id=target_entity_id,
                actor_id=actor_id,
                reason=reason,
                metadata_=dict(metadata or {}),
            )
        )
    write_audit_event(
        session,
        actor=actor_id or "system",
        action=f"entity_{action}",
        target_type="entity",
        target_id=source_entity_id,
        before=None,
        after={"target_entity_id": target_entity_id, "reason": reason, **(metadata or {})},
    )
