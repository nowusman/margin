"""Unified Architecture Governance HTTP surface."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy import select

from app.authz import (
    permitted_governance_domains,
    permitted_governance_record_types,
    permitted_source_element_ids,
    require_entity_permission,
    require_governance_permission,
)
from app.catalog_helpers import can_read_idea_block, readable_entity_ids, readable_idea_block_ids, require_idea_block
from app.governance_review_routes import router as governance_review_router
from app.governance_schemas import (
    ArchitectureBacklogCompletion,
    ArchitectureBacklogCreate,
    EntityLinkCreate,
    GovernanceLifecycleOperation,
    GovernanceRecordCreate,
    GovernanceRecordPatch,
    GovernanceRelationCreate,
    GovernanceSupersession,
    GovernanceTransition,
    IdeaBlockLinkCreate,
    TechnicalDebtResolution,
    WaiverRenewal,
)
from app.source_routes import get_session_factory
from margin_db.governance import (
    create_review_tasks_for_findings,
    detect_blind_spots,
    detect_contradiction_candidates,
    governance_consistency_findings,
    review_task_to_dict,
)
from margin_db.governance_models import GovernanceRecord
from margin_db.governance_queries import (
    governance_record_details,
    governance_record_linked_ids,
    governance_record_query,
    governance_record_source_element_ids,
    list_governance_records,
)
from margin_db.governance_records import (
    add_record_relation,
    create_record,
    link_entity,
    link_idea_block,
    unlink_entity,
    unlink_idea_block,
    update_mutable_fields,
)
from margin_db.governance_transitions import (
    accept_risk,
    close_risk,
    complete_architecture_backlog,
    create_architecture_backlog,
    renew_waiver,
    resolve_technical_debt,
    revoke_waiver,
    supersede_record,
    transition_record,
)
from margin_db.governance_types import LIFECYCLE_STATES, GovernanceError, validate_record_type
from margin_db.knowledge_models import IdeaBlock

router = APIRouter(prefix="/api/governance", tags=["governance"])


def _actor(request: Request) -> str:
    principal_id = getattr(request.state, "principal_id", None)
    return principal_id if isinstance(principal_id, str) and principal_id else "system"


def _correlation_id(request: Request) -> str | None:
    value = request.headers.get("x-correlation-id")
    return value.strip() if value and value.strip() else None


def _record_for_operation(session, request: Request, record_id: str, operation: str) -> GovernanceRecord:
    """Read only record scope before authorizing its full aggregate query."""

    scope = session.execute(
        select(GovernanceRecord.domain_id, GovernanceRecord.record_type).where(GovernanceRecord.id == record_id)
    ).one_or_none()
    if scope is None:
        raise HTTPException(status_code=404, detail="governance record not found")
    require_governance_permission(
        permissions=request.state.permissions,
        domain_id=scope.domain_id,
        record_type=scope.record_type,
        operation=operation,
    )
    record = session.get(GovernanceRecord, record_id, with_for_update=operation != "read")
    if record is None:
        raise HTTPException(status_code=404, detail="governance record not found")
    return record


def _require_current_revision(record: GovernanceRecord, expected_revision_id: str) -> None:
    if record.current_revision_id != expected_revision_id:
        raise HTTPException(status_code=409, detail="governance record has a newer revision; refresh and retry")


def _governance_review_scope(request: Request) -> tuple[frozenset[str], frozenset[str] | None]:
    record_types = permitted_governance_record_types(request.state.permissions, "approve")
    domains = permitted_governance_domains(request.state.permissions, "approve")
    if not record_types or domains == frozenset():
        raise HTTPException(status_code=403, detail="missing review access for governance records")
    return record_types, domains


def _record_detail(session, request: Request, record: GovernanceRecord) -> dict[str, object]:
    return _record_details(session, request, [record])[0]


def _record_details(session, request: Request, records: list[GovernanceRecord]) -> list[dict[str, object]]:
    source_ids = governance_record_source_element_ids(session, records)
    readable_source_ids = permitted_source_element_ids(
        session, source_ids, permissions=request.state.permissions, action="read"
    )
    idea_block_ids, entity_ids, related_record_ids = governance_record_linked_ids(session, records)
    blocks = list(session.scalars(select(IdeaBlock).where(IdeaBlock.id.in_(idea_block_ids))).all())
    readable_idea_blocks = frozenset(readable_idea_block_ids(session, request, blocks))
    readable_entities = frozenset(readable_entity_ids(session, request, set(entity_ids)))
    allowed_types = permitted_governance_record_types(request.state.permissions, "read")
    allowed_domains = permitted_governance_domains(request.state.permissions, "read")
    readable_related_record_ids = frozenset(
        record.id
        for record in session.scalars(
            governance_record_query(allowed_record_types=allowed_types, allowed_domain_ids=allowed_domains).where(
                GovernanceRecord.id.in_(related_record_ids)
            )
        ).all()
    )
    return governance_record_details(
        session,
        records,
        allowed_source_element_ids=readable_source_ids,
        allowed_idea_block_ids=readable_idea_blocks,
        allowed_entity_ids=readable_entities,
        allowed_related_record_ids=readable_related_record_ids,
    )


@router.get("/records")
def governance_records(
    request: Request,
    record_type: str | None = None,
    lifecycle_status: str | None = None,
    owner_id: str | None = None,
    domain_id: str | None = None,
    external_issue_id: str | None = None,
) -> dict[str, object]:
    allowed_types = permitted_governance_record_types(request.state.permissions, "read")
    allowed_domains = permitted_governance_domains(request.state.permissions, "read")
    if not allowed_types or allowed_domains == frozenset():
        raise HTTPException(status_code=403, detail="missing read access for governance records")
    if record_type and record_type not in allowed_types:
        raise HTTPException(status_code=403, detail="missing read access for governance record type")
    if lifecycle_status:
        allowed_statuses = (
            LIFECYCLE_STATES[record_type] if record_type is not None else frozenset().union(*LIFECYCLE_STATES.values())
        )
        if lifecycle_status not in allowed_statuses:
            scope_label = f"{record_type} " if record_type else ""
            raise HTTPException(status_code=422, detail=f"unknown {scope_label}governance lifecycle status")
    if domain_id and allowed_domains is not None and domain_id not in allowed_domains:
        raise HTTPException(status_code=403, detail=f"missing read access for domain {domain_id}")
    with get_session_factory(request)() as session:
        records = list_governance_records(
            session,
            record_type=record_type,
            lifecycle_status=lifecycle_status,
            owner_id=owner_id,
            domain_id=domain_id,
            external_issue_id=external_issue_id,
            allowed_record_types=allowed_types,
            allowed_domain_ids=allowed_domains,
        )
        return {"records": _record_details(session, request, records)}


@router.post("/records", status_code=201)
def create_governance_record(body: GovernanceRecordCreate, request: Request) -> dict[str, object]:
    try:
        record_type = validate_record_type(body.record_type)
        require_governance_permission(
            permissions=request.state.permissions, domain_id=body.domain_id, record_type=record_type, operation="create"
        )
        with get_session_factory(request)() as session:
            record = create_record(
                session,
                **body.model_dump(exclude={"external_issue_url"}),
                external_issue_url=str(body.external_issue_url) if body.external_issue_url else None,
                actor=_actor(request),
                correlation_id=_correlation_id(request),
            )
            session.commit()
            return {"record": _record_detail(session, request, record)}
    except GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/records/{record_id}")
def governance_record(record_id: str, request: Request) -> dict[str, object]:
    with get_session_factory(request)() as session:
        record = _record_for_operation(session, request, record_id, "read")
        return {"record": _record_detail(session, request, record)}


@router.patch("/records/{record_id}")
def patch_governance_record(record_id: str, body: GovernanceRecordPatch, request: Request) -> dict[str, object]:
    try:
        with get_session_factory(request)() as session:
            record = _record_for_operation(session, request, record_id, "manage")
            _require_current_revision(record, body.expected_revision_id)
            changes = body.model_dump(exclude={"reason", "expected_revision_id"}, exclude_unset=True)
            if "external_issue_url" in changes:
                changes["external_issue_url"] = (
                    str(changes["external_issue_url"]) if changes["external_issue_url"] else None
                )
            update_mutable_fields(
                session,
                record=record,
                changes=changes,
                actor=_actor(request),
                reason=body.reason,
                expected_revision_id=body.expected_revision_id,
                correlation_id=_correlation_id(request),
            )
            session.commit()
            return {"record": _record_detail(session, request, record)}
    except GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/records/{record_id}/transitions")
def transition_governance_record(record_id: str, body: GovernanceTransition, request: Request) -> dict[str, object]:
    try:
        operation = "approve" if body.target_status in {"accepted", "approved"} else "transition"
        with get_session_factory(request)() as session:
            record = _record_for_operation(session, request, record_id, operation)
            _require_current_revision(record, body.expected_revision_id)
            transition_record(
                session,
                record=record,
                target_status=body.target_status,
                actor=_actor(request),
                reason=body.reason,
                expected_revision_id=body.expected_revision_id,
                attributes=body.attributes,
                correlation_id=_correlation_id(request),
            )
            session.commit()
            return {"record": _record_detail(session, request, record)}
    except GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/records/{record_id}/waiver-renewals")
def renew_governance_waiver(record_id: str, body: WaiverRenewal, request: Request) -> dict[str, object]:
    try:
        with get_session_factory(request)() as session:
            record = _record_for_operation(session, request, record_id, "approve")
            _require_current_revision(record, body.expected_revision_id)
            renew_waiver(
                session,
                record=record,
                expiry_date=body.expiry_date,
                actor=_actor(request),
                reason=body.reason,
                expected_revision_id=body.expected_revision_id,
                correlation_id=_correlation_id(request),
            )
            session.commit()
            return {"record": _record_detail(session, request, record)}
    except GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/records/{record_id}/waiver-revocations")
def revoke_governance_waiver(record_id: str, body: GovernanceLifecycleOperation, request: Request) -> dict[str, object]:
    try:
        with get_session_factory(request)() as session:
            record = _record_for_operation(session, request, record_id, "approve")
            _require_current_revision(record, body.expected_revision_id)
            revoke_waiver(
                session,
                record=record,
                actor=_actor(request),
                reason=body.reason,
                expected_revision_id=body.expected_revision_id,
                correlation_id=_correlation_id(request),
            )
            session.commit()
            return {"record": _record_detail(session, request, record)}
    except GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/records/{record_id}/risk-acceptances")
def accept_governance_risk(record_id: str, body: GovernanceLifecycleOperation, request: Request) -> dict[str, object]:
    try:
        with get_session_factory(request)() as session:
            record = _record_for_operation(session, request, record_id, "approve")
            _require_current_revision(record, body.expected_revision_id)
            accept_risk(
                session,
                record=record,
                actor=_actor(request),
                reason=body.reason,
                expected_revision_id=body.expected_revision_id,
                correlation_id=_correlation_id(request),
            )
            session.commit()
            return {"record": _record_detail(session, request, record)}
    except GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/records/{record_id}/risk-closures")
def close_governance_risk(record_id: str, body: GovernanceLifecycleOperation, request: Request) -> dict[str, object]:
    try:
        with get_session_factory(request)() as session:
            record = _record_for_operation(session, request, record_id, "transition")
            _require_current_revision(record, body.expected_revision_id)
            close_risk(
                session,
                record=record,
                actor=_actor(request),
                reason=body.reason,
                expected_revision_id=body.expected_revision_id,
                correlation_id=_correlation_id(request),
            )
            session.commit()
            return {"record": _record_detail(session, request, record)}
    except GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/records/{record_id}/technical-debt-resolutions")
def resolve_governance_technical_debt(
    record_id: str, body: TechnicalDebtResolution, request: Request
) -> dict[str, object]:
    try:
        with get_session_factory(request)() as session:
            record = _record_for_operation(session, request, record_id, "transition")
            _require_current_revision(record, body.expected_revision_id)
            resolve_technical_debt(
                session,
                record=record,
                resolution_note=body.resolution_note,
                actor=_actor(request),
                reason=body.reason,
                expected_revision_id=body.expected_revision_id,
                correlation_id=_correlation_id(request),
            )
            session.commit()
            return {"record": _record_detail(session, request, record)}
    except GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/records/{record_id}/architecture-backlog-completions")
def complete_governance_backlog(
    record_id: str, body: ArchitectureBacklogCompletion, request: Request
) -> dict[str, object]:
    try:
        with get_session_factory(request)() as session:
            record = _record_for_operation(session, request, record_id, "transition")
            _require_current_revision(record, body.expected_revision_id)
            complete_architecture_backlog(
                session,
                record=record,
                completion_note=body.completion_note,
                actor=_actor(request),
                reason=body.reason,
                expected_revision_id=body.expected_revision_id,
                correlation_id=_correlation_id(request),
            )
            session.commit()
            return {"record": _record_detail(session, request, record)}
    except GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/records/{record_id}/supersede")
def supersede_governance_record(record_id: str, body: GovernanceSupersession, request: Request) -> dict[str, object]:
    try:
        with get_session_factory(request)() as session:
            record = _record_for_operation(session, request, record_id, "transition")
            _require_current_revision(record, body.expected_revision_id)
            successor = _record_for_operation(session, request, body.target_record_id, "transition")
            _require_current_revision(successor, body.expected_target_revision_id)
            supersede_record(
                session,
                record=record,
                successor=successor,
                actor=_actor(request),
                reason=body.reason,
                expected_source_revision_id=body.expected_revision_id,
                expected_successor_revision_id=body.expected_target_revision_id,
                correlation_id=_correlation_id(request),
            )
            session.commit()
            return {"record": _record_detail(session, request, record)}
    except GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/records/{record_id}/relations")
def create_governance_relation(record_id: str, body: GovernanceRelationCreate, request: Request) -> dict[str, object]:
    try:
        with get_session_factory(request)() as session:
            source = _record_for_operation(session, request, record_id, "manage")
            _require_current_revision(source, body.expected_revision_id)
            target = _record_for_operation(session, request, body.target_record_id, "manage")
            _require_current_revision(target, body.expected_target_revision_id)
            if body.status != "candidate":
                require_governance_permission(
                    permissions=request.state.permissions,
                    domain_id=source.domain_id,
                    record_type=source.record_type,
                    operation="approve",
                )
                require_governance_permission(
                    permissions=request.state.permissions,
                    domain_id=target.domain_id,
                    record_type=target.record_type,
                    operation="approve",
                )
            relation = add_record_relation(
                session,
                source=source,
                target=target,
                relation_type=body.relation_type,
                actor=_actor(request),
                status=body.status,
                valid_from=body.valid_from,
                valid_to=body.valid_to,
                provenance={"reason": body.reason},
                expected_source_revision_id=body.expected_revision_id,
                expected_target_revision_id=body.expected_target_revision_id,
                correlation_id=_correlation_id(request),
            )
            session.commit()
            return {"relation_id": relation.id}
    except GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/records/{record_id}/idea-blocks", status_code=201)
def create_idea_block_link(record_id: str, body: IdeaBlockLinkCreate, request: Request) -> dict[str, object]:
    try:
        with get_session_factory(request)() as session:
            record = _record_for_operation(session, request, record_id, "manage")
            _require_current_revision(record, body.expected_revision_id)
            idea_block = require_idea_block(session, body.idea_block_id)
            if not can_read_idea_block(session, request, idea_block):
                raise HTTPException(status_code=403, detail="missing read access for IdeaBlock")
            link = link_idea_block(
                session,
                record=record,
                idea_block_id=body.idea_block_id,
                role=body.role,
                display_order=body.display_order,
                actor=_actor(request),
                reason=body.reason,
                expected_revision_id=body.expected_revision_id,
                correlation_id=_correlation_id(request),
            )
            session.commit()
            return {"idea_block": {"id": link.idea_block_id, "role": link.role, "display_order": link.display_order}}
    except GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/records/{record_id}/idea-blocks/{idea_block_id}", status_code=204)
def delete_idea_block_link(
    record_id: str, idea_block_id: str, role: str, reason: str, expected_revision_id: str, request: Request
) -> Response:
    try:
        with get_session_factory(request)() as session:
            record = _record_for_operation(session, request, record_id, "manage")
            _require_current_revision(record, expected_revision_id)
            unlink_idea_block(
                session,
                record=record,
                idea_block_id=idea_block_id,
                role=role,
                actor=_actor(request),
                reason=reason,
                expected_revision_id=expected_revision_id,
                correlation_id=_correlation_id(request),
            )
            session.commit()
        return Response(status_code=204)
    except GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/records/{record_id}/entities", status_code=201)
def create_entity_link(record_id: str, body: EntityLinkCreate, request: Request) -> dict[str, object]:
    try:
        with get_session_factory(request)() as session:
            record = _record_for_operation(session, request, record_id, "manage")
            _require_current_revision(record, body.expected_revision_id)
            require_entity_permission(session, body.entity_id, permissions=request.state.permissions, action="read")
            link = link_entity(
                session,
                record=record,
                entity_id=body.entity_id,
                role=body.role,
                actor=_actor(request),
                reason=body.reason,
                expected_revision_id=body.expected_revision_id,
                correlation_id=_correlation_id(request),
            )
            session.commit()
            return {"entity": {"id": link.entity_id, "role": link.role}}
    except GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/records/{record_id}/entities/{entity_id}", status_code=204)
def delete_entity_link(
    record_id: str, entity_id: str, role: str, reason: str, expected_revision_id: str, request: Request
) -> Response:
    try:
        with get_session_factory(request)() as session:
            record = _record_for_operation(session, request, record_id, "manage")
            _require_current_revision(record, expected_revision_id)
            unlink_entity(
                session,
                record=record,
                entity_id=entity_id,
                role=role,
                actor=_actor(request),
                reason=reason,
                expected_revision_id=expected_revision_id,
                correlation_id=_correlation_id(request),
            )
            session.commit()
        return Response(status_code=204)
    except GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/records/{record_id}/architecture-backlog", status_code=201)
def create_backlog(record_id: str, body: ArchitectureBacklogCreate, request: Request) -> dict[str, object]:
    try:
        with get_session_factory(request)() as session:
            origin = _record_for_operation(session, request, record_id, "manage")
            _require_current_revision(origin, body.expected_origin_revision_id)
            require_governance_permission(
                permissions=request.state.permissions,
                domain_id=origin.domain_id,
                record_type="architecture_backlog",
                operation="create",
            )
            # The explicit operation creates a verified ``corrects`` relation
            # as part of the backlog aggregate, so both sides require review
            # authority in addition to write authority.
            require_governance_permission(
                permissions=request.state.permissions,
                domain_id=origin.domain_id,
                record_type=origin.record_type,
                operation="approve",
            )
            require_governance_permission(
                permissions=request.state.permissions,
                domain_id=origin.domain_id,
                record_type="architecture_backlog",
                operation="approve",
            )
            backlog = create_architecture_backlog(
                session,
                origin=origin,
                business_key=body.business_key,
                title=body.title,
                pending_correction=body.pending_correction,
                actor=_actor(request),
                reason=body.reason,
                priority=body.priority,
                target_date=body.target_date,
                expected_origin_revision_id=body.expected_origin_revision_id,
                correlation_id=_correlation_id(request),
            )
            session.commit()
            return {"record": _record_detail(session, request, backlog)}
    except GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/health/lint-report")
def lint_report(request: Request, create_tasks: bool = False) -> dict[str, object]:
    record_types, domain_ids = _governance_review_scope(request)
    with get_session_factory(request)() as session:
        findings = governance_consistency_findings(
            session, allowed_record_types=record_types, allowed_domain_ids=domain_ids
        )
        tasks = create_review_tasks_for_findings(session, findings) if create_tasks else []
        if tasks:
            session.commit()
        return {
            "findings": [finding.to_dict() for finding in findings],
            "review_tasks_created": [str(task.id) for task in tasks],
        }


@router.post("/contradictions/detect")
def contradiction_detect(request: Request) -> dict[str, object]:
    record_types, domain_ids = _governance_review_scope(request)
    with get_session_factory(request)() as session:
        tasks = detect_contradiction_candidates(
            session, allowed_record_types=record_types, allowed_domain_ids=domain_ids
        )
        session.commit()
        return {"review_tasks": [review_task_to_dict(task) for task in tasks]}


@router.post("/blind-spots/detect")
def blind_spot_detect(request: Request) -> dict[str, object]:
    record_types, domain_ids = _governance_review_scope(request)
    with get_session_factory(request)() as session:
        tasks = detect_blind_spots(session, allowed_record_types=record_types, allowed_domain_ids=domain_ids)
        session.commit()
        return {"review_tasks": [review_task_to_dict(task) for task in tasks]}


router.include_router(governance_review_router)
