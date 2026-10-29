"""Thin Home-page aggregate endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, or_, select

from app.catalog_helpers import (
    entity_source_classification_filter as _entity_source_classification_filter,
    has_any_permission as _has_any_permission,
    require_any_read as _require_any_read,
)
from app.source_routes import get_session_factory
from margin_db.entity_models import Entity
from margin_db.governance import list_review_tasks
from margin_db.governance_queries import governance_dashboard_metrics
from margin_db.knowledge_models import IdeaBlock, IdeaBlockEntity, IdeaBlockRevision
from margin_db.review_models import ReviewTask
from margin_db.source_models import SourceDocument
from margin_db.user_models import Domain

router = APIRouter(prefix="/api", tags=["catalog"])


def _review_task_domain(session, task: ReviewTask) -> str | None:
    metadata = task.metadata_ or {}
    meta_domain = metadata.get("domain_id")
    if isinstance(meta_domain, str) and meta_domain:
        return meta_domain
    if task.target_type == "entity":
        entity = session.get(Entity, task.target_id)
        return entity.domain_id if entity is not None else None
    if task.target_type == "source_document":
        source = session.get(SourceDocument, task.target_id)
        return source.domain_id if source is not None else None
    return None


@router.get("/dashboard/heatmap")
def dashboard_heatmap(request: Request, domain_id: str | None = Query(default=None)) -> dict[str, object]:
    session_factory = get_session_factory(request)
    with session_factory() as session:
        domains = session.execute(select(Domain).order_by(Domain.id)).scalars().all()
        cards: list[dict[str, object]] = []
        for domain in domains:
            if domain_id and domain.id != domain_id:
                continue
            current_domain_id = domain.id
            if not request.state.permissions.allows_domain(current_domain_id, "read"):
                continue
            governance_metrics = governance_dashboard_metrics(session, current_domain_id)
            risk_volume = governance_metrics["risk_volume"]
            waiver_volume = governance_metrics["waiver_volume"]
            debt_volume = governance_metrics["debt_volume"]
            high_severity_risks = governance_metrics["high_severity_risks"]
            stale_statement = (
                select(func.count(func.distinct(IdeaBlock.id)))
                .join(IdeaBlockEntity, IdeaBlockEntity.idea_block_id == IdeaBlock.id)
                .join(
                    IdeaBlockRevision,
                    (IdeaBlockRevision.id == IdeaBlock.current_revision_id)
                    & (IdeaBlockRevision.idea_block_id == IdeaBlock.id),
                )
                .join(Entity, Entity.id == IdeaBlockEntity.entity_id)
                .where(_entity_source_classification_filter(domain_id=current_domain_id))
                .where(IdeaBlockRevision.publication_status != "verified")
            )
            stale_claims = int(session.scalar(stale_statement) or 0)
            expired_waivers = governance_metrics["expired_waivers"]
            unowned_entities = int(
                session.scalar(
                    select(func.count())
                    .select_from(Entity)
                    .where(_entity_source_classification_filter(domain_id=current_domain_id))
                    .where(or_(Entity.owner_user_id.is_(None), Entity.owner_user_id == ""))
                    .where(or_(Entity.steward_user_id.is_(None), Entity.steward_user_id == ""))
                )
                or 0
            )
            missing_adr_coverage = governance_metrics["missing_adr_coverage"]
            atdi = float(debt_volume)
            health = max(
                0,
                int(
                    100
                    - (risk_volume * 4)
                    - (high_severity_risks * 6)
                    - (stale_claims * 2)
                    - (expired_waivers * 5)
                    - (unowned_entities * 3)
                    - (missing_adr_coverage * 4)
                    - atdi
                ),
            )
            cards.append(
                {
                    "domain_id": current_domain_id,
                    "domain_name": domain.name,
                    "health": health,
                    "risk_volume": risk_volume,
                    "risks": risk_volume,
                    "high_severity_risks": high_severity_risks,
                    "waivers": waiver_volume,
                    "debt": debt_volume,
                    "atdi": atdi,
                    "stale_claims": stale_claims,
                    "stale_items": stale_claims,
                    "expired_waivers": expired_waivers,
                    "unowned_entities": unowned_entities,
                    "missing_adr_coverage": missing_adr_coverage,
                }
            )
        return {"domains": cards}


@router.get("/dashboard/summary")
def dashboard_summary(request: Request, domain_id: str | None = Query(default=None)) -> dict[str, object]:
    _require_any_read(request)
    session_factory = get_session_factory(request)
    with session_factory() as session:
        domains = [
            domain
            for domain in session.execute(select(Domain).order_by(Domain.id)).scalars().all()
            if request.state.permissions.allows_domain(domain.id, "read")
        ]
        if domain_id:
            domains = [domain for domain in domains if domain.id == domain_id]
        metrics = [governance_dashboard_metrics(session, domain.id) for domain in domains]
        visible_domain_ids = {domain.id for domain in domains}
        review_tasks = list_review_tasks(session, defer_evidence=True) if _has_any_permission(request, "review") else []
        review_tasks = [task for task in review_tasks if _review_task_domain(session, task) in visible_domain_ids]
        return {
            "review_tasks_assigned": len(review_tasks),
            "stale_claims": len([task for task in review_tasks if task.status != "closed"]),
            "expired_waivers": sum(int(item["expired_waivers"]) for item in metrics),
            "recent_adrs": sorted(
                (record for item in metrics for record in item["recent_adrs"]),
                key=lambda record: str(record["updated_at"]),
                reverse=True,
            )[:5],
            "top_risks": sorted(
                (record for item in metrics for record in item["top_risks"]),
                key=lambda record: (record["severity"] is not None, record["severity"] or -1),
                reverse=True,
            )[:5],
            "top_debt": sorted(
                (record for item in metrics for record in item["top_debt"]),
                key=lambda record: str(record["updated_at"]),
                reverse=True,
            )[:5],
        }
