"""Version-one governed Search and Ask Margin API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.assistant_schemas import RetrievalFilters, RetrievalRequest
from app.authz import require_any_domain_permission
from app.retrieval_runtime import configure_retrieval_service
from app.source_routes import get_session_factory
from margin_db.entity_models import Entity
from margin_db.governance_types import LIFECYCLE_STATES
from margin_db.personal import get_thread, record_exchange, thread_messages
from margin_db.personal_models import AssistantThread
from margin_db.seed import IDEA_BLOCK_TYPE_TERMS
from margin_retrieval.interfaces import EvidenceItem, RetrievalExpansion, RetrievalScope, SearchResult
from margin_retrieval.language import detect_output_language
from margin_retrieval.policy import RetrievalPermissionError, ScopeAuthorizer
from margin_retrieval.security import detect_prompt_injection, sanitize_user_query
from margin_retrieval.service import RetrievalService
from margin_retrieval.support import citation_dict, scope_dict, snippet_for
from margin_schema.ontology import (
    ALL_ENTITY_KINDS,
    ENTITY_RELATIONSHIP_TYPES,
    IDEA_BLOCK_RELATIONSHIP_TYPES,
    ONTOLOGY_VERSION,
)

router = APIRouter(tags=["retrieval"])


def _retrieval_service(request: Request) -> RetrievalService:
    service = getattr(request.app.state, "retrieval_service", None)
    if service is None:
        configure_retrieval_service(request.app)
        service = getattr(request.app.state, "retrieval_service", None)
    if service is None:
        detail = getattr(request.app.state, "retrieval_service_error", "")
        raise HTTPException(status_code=503, detail=f"retrieval service unavailable{': ' + detail if detail else ''}")
    return service


def _scope(filters: RetrievalFilters) -> RetrievalScope:
    return RetrievalScope(
        domain_ids=tuple(filters.domain_ids),
        category_ids=tuple(filters.category_ids),
        entity_ids=tuple(filters.entity_ids),
        entity_terms=tuple(filters.entity_terms),
        entity_types=tuple(filters.entity_types),
        relationship_types=tuple(filters.relationship_types),
        idea_block_types=tuple(filters.idea_block_types),
        source_document_ids=tuple(filters.source_document_ids),
        sensitivities=tuple(filters.sensitivities),
        statuses=tuple(filters.statuses),
        date_from=filters.valid_from,
        date_to=filters.valid_to,
        governance_record_types=tuple(filters.governance_record_types),
        governance_statuses=tuple(filters.governance_statuses),
        architecture_backlog_states=tuple(filters.architecture_backlog_states),
    )


def _expansion(body: RetrievalRequest) -> RetrievalExpansion:
    return RetrievalExpansion(
        relation_types=tuple(body.expand.relation_types), depth=body.expand.depth, limit=body.expand.limit
    )


def _query(body: RetrievalRequest) -> str:
    query = sanitize_user_query(body.query)
    if detect_prompt_injection(query):
        raise HTTPException(status_code=422, detail="query blocked by prompt-injection guardrails")
    return query


def _result_payload(result: SearchResult) -> dict[str, object]:
    return {
        "contract_version": "v1",
        "query": result.query,
        "response_mode": result.response_mode,
        "results": [_result_card(item, query=result.query) for item in result.results],
        "applied_filters": _applied_filters(result.applied_scope),
        "fusion": _fusion_breakdown(result.stage_metrics),
        "ambiguity": {
            "entity_terms": [
                {
                    "term": entry["term"],
                    "status": entry["status"],
                    "candidate_entity_ids": [candidate["id"] for candidate in entry["candidates"]],
                    "candidate_types": sorted({candidate["type"] for candidate in entry["candidates"]}),
                }
                for entry in result.ambiguity
            ]
        },
        "operational_warnings": list(result.warnings),
        "stage_timings": {stage: int(metric.get("latency_ms", 0)) for stage, metric in result.stage_metrics.items()},
        "expansion": {
            "status": result.expansion.status,
            "relation_types": list(result.expansion.relation_types),
            "depth": result.expansion.depth,
            "results": [_result_card(item, query=result.query) for item in result.expansion.results],
        },
    }


def _result_card(item: EvidenceItem, *, query: str = "") -> dict[str, object]:
    metadata = item.metadata
    relations = metadata.get("relations", [])
    return {
        "idea_block_id": metadata.get("idea_block_id"),
        "idea_block_revision_id": metadata.get("idea_block_revision_id"),
        "score": item.score,
        "statement": item.text,
        "snippet": snippet_for(item.text, query) if query else item.text,
        "type": metadata.get("type", ""),
        "status": metadata.get("publication_status", ""),
        "polarity": metadata.get("polarity", ""),
        "valid_from": metadata.get("valid_from"),
        "valid_to": metadata.get("valid_to"),
        "entities": [
            {"id": entity.get("id"), "name": entity.get("label"), "type": entity.get("type")}
            for entity in metadata.get("entities", [])
            if isinstance(entity, dict)
        ],
        "citations": [citation_dict(citation) for citation in item.citations],
        "contradictions": _relations(relations, "contradicts"),
        "supersession": _relations(relations, "supersedes", "superseded_by"),
        "governance_records": list(metadata.get("governance", [])),
        "related": _related_records(metadata),
    }


def _related_records(metadata: dict[str, object]) -> list[dict[str, object]]:
    """Return the records a statement refers to, so a hit opens a neighbourhood."""

    related: list[dict[str, object]] = []
    entities = metadata.get("entities")
    for entity in entities if isinstance(entities, list) else []:
        if not isinstance(entity, dict):
            continue
        related.append(
            {"kind": "entity", "id": entity.get("id"), "label": entity.get("label"), "type": entity.get("type")}
        )
    governance = metadata.get("governance")
    for record in governance if isinstance(governance, list) else []:
        if not isinstance(record, dict):
            continue
        related.append(
            {
                "kind": "governance",
                "id": record.get("id"),
                "label": record.get("title"),
                "type": record.get("record_type"),
            }
        )
    return related


def _fusion_breakdown(stage_metrics: dict[str, dict[str, object]]) -> dict[str, object]:
    """Report how the result set was assembled, so retrieval can be explained."""

    fusion = stage_metrics.get("rrf_fusion")
    if not isinstance(fusion, dict):
        return {"mode": "unknown", "channels": {}}
    metadata = fusion.get("metadata")
    channels = metadata.get("channels") if isinstance(metadata, dict) else None
    return {
        "mode": metadata.get("mode", "unknown") if isinstance(metadata, dict) else "unknown",
        "channels": dict(channels) if isinstance(channels, dict) else {},
        "count": fusion.get("count", 0),
    }


def _relations(value: object, *relation_types: str) -> list[dict[str, object]]:
    if not isinstance(value, list | tuple):
        return []
    return [relation for relation in value if isinstance(relation, dict) and relation.get("type") in relation_types]


def _applied_filters(scope: RetrievalScope) -> dict[str, object]:
    values = scope_dict(scope)
    return {
        "domain_ids": values["domain_ids"],
        "category_ids": values["category_ids"],
        "entity_ids": values["entity_ids"],
        "entity_terms": values["entity_terms"],
        "entity_types": values["entity_types"],
        "relationship_types": values["relationship_types"],
        "idea_block_types": values["idea_block_types"],
        "source_document_ids": values["source_document_ids"],
        "sensitivities": values["sensitivities"],
        "statuses": values["statuses"],
        "valid_from": values["date_from"],
        "valid_to": values["date_to"],
        "governance_record_types": values["governance_record_types"],
        "governance_statuses": values["governance_statuses"],
        "architecture_backlog_states": values["architecture_backlog_states"],
    }


def _handle_retrieval_error(error: RetrievalPermissionError) -> None:
    detail = str(error)
    lowered = detail.lower()
    permission_markers = (
        "outside caller permissions",
        "missing read",
        "no readable",
        "requires review permission",
        "no longer has",
    )
    if any(marker in lowered for marker in permission_markers):
        raise HTTPException(status_code=403, detail=detail) from error
    validation_markers = ("unknown", "unsupported", "invalid", "must", "requires", "includes", "limit", "depth")
    if any(marker in lowered for marker in validation_markers):
        raise HTTPException(status_code=422, detail=detail) from error
    raise HTTPException(status_code=403, detail=detail) from error


@router.post("/api/search")
def search(body: RetrievalRequest, request: Request) -> dict[str, object]:
    try:
        with get_session_factory(request)() as session:
            result = _retrieval_service(request).search(
                session,
                query=_query(body),
                scope=_scope(body.filters),
                permissions=request.state.permissions,
                top_k=body.top_k,
                expansion=_expansion(body),
            )
            return _result_payload(result)
    except RetrievalPermissionError as error:
        _handle_retrieval_error(error)
    raise AssertionError("unreachable")


@router.post("/api/ask")
def ask(body: RetrievalRequest, request: Request) -> dict[str, object]:
    try:
        with get_session_factory(request)() as session:
            thread = _thread_for(body, request, session)
            history = _conversation_history(thread, session)
            result = _retrieval_service(request).ask(
                session,
                query=_query(body),
                scope=_scope(body.filters),
                permissions=request.state.permissions,
                top_k=body.top_k,
                expansion=_expansion(body),
                output_language=body.output_language or detect_output_language(body.query),
                context_budget_chars=body.context_budget_chars,
                conversation_history=history,
            )
            payload = _result_payload(result)
            payload.update(
                {
                    "answer": result.answer.markdown,
                    "answer_citation_ids": list(result.answer.citation_ids),
                    "answer_unavailable_reason": result.answer.unavailable_reason,
                    "answer_validation": result.answer.validation,
                    "answer_latency_ms": result.answer.latency_ms,
                    "model": result.answer.model,
                    "output_language": body.output_language or detect_output_language(body.query),
                    "thread_id": thread.id if thread is not None else None,
                }
            )
            if thread is not None:
                record_exchange(
                    session,
                    thread=thread,
                    question=body.query,
                    answer=result.answer.markdown,
                    citation_ids=list(result.answer.citation_ids),
                )
                session.commit()
            return payload
    except RetrievalPermissionError as error:
        _handle_retrieval_error(error)
    raise AssertionError("unreachable")


def _thread_for(body: RetrievalRequest, request: Request, session) -> AssistantThread | None:
    """Return the conversation this answer belongs to, when one was named."""

    if not body.thread_id:
        return None
    principal_id = getattr(request.state, "principal_id", "")
    if not principal_id or principal_id == "anonymous":
        raise HTTPException(status_code=403, detail="a named principal is required for a conversation")
    thread = get_thread(session, principal_id, body.thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return thread


def _conversation_history(thread, session) -> tuple[str, ...]:
    """Return the earlier turns of a conversation as context."""

    if thread is None:
        return ()
    return tuple(f"{message.role}: {message.content}" for message in thread_messages(session, thread.id))


@router.post("/api/search/source-discovery")
def discover_sources(body: RetrievalRequest, request: Request) -> dict[str, object]:
    if body.expand.depth or body.expand.limit or body.expand.relation_types:
        raise HTTPException(status_code=422, detail="source discovery does not support relationship expansion")
    try:
        with get_session_factory(request)() as session:
            result = _retrieval_service(request).discover_sources(
                session,
                query=_query(body),
                scope=_scope(body.filters),
                permissions=request.state.permissions,
                top_k=body.top_k,
            )
            return _result_payload(result)
    except RetrievalPermissionError as error:
        _handle_retrieval_error(error)
    raise AssertionError("unreachable")


@router.get("/api/retrieval/filter-options")
def filter_options(request: Request) -> dict[str, object]:
    require_any_domain_permission(
        permissions=request.state.permissions, action="read", detail="missing read access for retrieval filters"
    )
    with get_session_factory(request)() as session:
        categories = [
            {"id": entity.id, "label": entity.preferred_label}
            for entity in session.scalars(
                select(Entity)
                .where(Entity.ontology_type == "category", Entity.lifecycle_status == "active")
                .order_by(Entity.preferred_label, Entity.id)
            )
            if request.state.permissions.allows_category(entity.preferred_label, "read")
            and (entity.domain_id is None or request.state.permissions.allows_domain(entity.domain_id, "read"))
        ]
    authorizer = ScopeAuthorizer()
    sensitivities = sorted(authorizer.BASELINE_SENSITIVITIES)
    permitted_sensitivities = [
        sensitivity
        for sensitivity in sensitivities
        if request.state.permissions.allows_sensitivity(sensitivity, "read")
    ]
    governance_types = [
        record_type
        for record_type in sorted(LIFECYCLE_STATES)
        if request.state.permissions.allows_category(f"governance:{record_type}", "read")
    ]
    backlog_states = (
        sorted(LIFECYCLE_STATES["architecture_backlog"]) if "architecture_backlog" in governance_types else []
    )
    return {
        "ontology_version": ONTOLOGY_VERSION,
        "categories": categories,
        "entity_types": sorted(ALL_ENTITY_KINDS),
        "relationship_types": sorted(ENTITY_RELATIONSHIP_TYPES),
        "expansion_relationship_types": sorted(ENTITY_RELATIONSHIP_TYPES | IDEA_BLOCK_RELATIONSHIP_TYPES),
        "idea_block_types": sorted(IDEA_BLOCK_TYPE_TERMS),
        "idea_block_statuses": sorted(authorizer.BASELINE_STATUSES),
        "governance_record_types": governance_types,
        "governance_statuses": {record_type: sorted(LIFECYCLE_STATES[record_type]) for record_type in governance_types},
        "architecture_backlog_states": backlog_states,
        "sensitivities": permitted_sensitivities,
    }
