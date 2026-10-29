"""Governed export of a domain's canonical records.

An export is scoped to what the requester may read. The scope is captured when
the export is requested, while their permissions are known, and applied when
the archive is composed. An export can therefore never carry more than the
requester was able to read.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.authz import permitted_governance_record_types, require_permission
from app.catalog_helpers import require_any_read
from app.source_routes import get_session_factory
from margin_db.export import (
    ExportScope,
    complete_export,
    fail_export,
    load_export,
    render_export_archive,
    request_export,
)
from margin_db.export_models import EXPORT_STATE_FAILED, EXPORT_STATE_READY

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/exports", tags=["exports"])


class ExportRequestBody(BaseModel):
    model_config = {"extra": "forbid"}

    domain_id: str = Field(min_length=1, max_length=200)


def _readable_scope(request: Request) -> ExportScope:
    """Return what the requester may read, as the export scope."""

    permissions = request.state.permissions
    return ExportScope(
        domain_ids=frozenset(
            domain_id for domain_id, actions in permissions.domain_permissions.items() if "read" in actions
        ),
        category_ids=frozenset(
            category for category, actions in permissions.category_permissions.items() if "read" in actions
        ),
        sensitivities=frozenset(
            sensitivity for sensitivity, actions in permissions.sensitivity_permissions.items() if "read" in actions
        ),
        governance_record_types=permitted_governance_record_types(permissions, "read"),
    )


def _export_payload(bundle) -> dict[str, object]:
    return {
        "export_id": bundle.id,
        "domain_id": bundle.domain_id,
        "state": bundle.state,
        "content_hash": bundle.content_hash,
        "manifest": bundle.manifest,
        "error": bundle.error,
        "created_at": bundle.created_at.isoformat() if bundle.created_at else None,
        "completed_at": bundle.completed_at.isoformat() if bundle.completed_at else None,
    }


@router.post("", status_code=202)
def create_export(body: ExportRequestBody, request: Request) -> dict[str, object]:
    """Request a portable export of one domain's records."""

    require_any_read(request)
    require_permission(
        permissions=request.state.permissions, domain_id=body.domain_id, category=None, sensitivity=None, action="read"
    )
    principal_id = getattr(request.state, "principal_id", "") or "anonymous"
    with get_session_factory(request)() as session:
        bundle = request_export(
            session, domain_id=body.domain_id, requested_by=principal_id, scope=_readable_scope(request)
        )
        session.commit()
        export_id = bundle.id
    _dispatch(request, export_id)
    return {"export_id": export_id, "state": bundle.state, "domain_id": body.domain_id}


def _require_export_access(request: Request, bundle) -> None:
    """Only the reader who asked for an export may read it.

    The scope an export was composed under is captured with it, so an archive
    belongs to the reader who asked for it and carries what they could read.
    """

    principal_id = getattr(request.state, "principal_id", "") or "anonymous"
    if bundle.requested_by != principal_id:
        raise HTTPException(status_code=404, detail="export not found")
    require_permission(
        permissions=request.state.permissions,
        domain_id=bundle.domain_id,
        category=None,
        sensitivity=None,
        action="read",
    )


@router.get("/{export_id}")
def read_export(export_id: str, request: Request) -> dict[str, object]:
    """Return the state and manifest of one export."""

    require_any_read(request)
    with get_session_factory(request)() as session:
        bundle = load_export(session, export_id)
        if bundle is None:
            raise HTTPException(status_code=404, detail="export not found")
        _require_export_access(request, bundle)
        return _export_payload(bundle)


@router.get("/{export_id}/manifest")
def read_export_manifest(export_id: str, request: Request) -> dict[str, object]:
    """Return the manifest of one export, listing exactly what it contains."""

    require_any_read(request)
    with get_session_factory(request)() as session:
        bundle = load_export(session, export_id)
        if bundle is None:
            raise HTTPException(status_code=404, detail="export not found")
        _require_export_access(request, bundle)
        if bundle.state != EXPORT_STATE_READY:
            raise HTTPException(status_code=409, detail=f"export is {bundle.state}")
        return {"export_id": bundle.id, "manifest": bundle.manifest}


@router.get("/{export_id}/download")
def download_export(export_id: str, request: Request) -> Response:
    """Download the export archive."""

    require_any_read(request)
    with get_session_factory(request)() as session:
        bundle = load_export(session, export_id)
        if bundle is None:
            raise HTTPException(status_code=404, detail="export not found")
        _require_export_access(request, bundle)
        if bundle.state != EXPORT_STATE_READY:
            raise HTTPException(status_code=409, detail=f"export is {bundle.state}")
        archive = render_export_archive(bundle)
        filename = f"margin-export-{bundle.domain_id}.zip"
    return Response(
        content=archive,
        media_type="application/zip",
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


def _dispatch(request: Request, export_id: str) -> None:
    """Ask the worker to compose the export, if a dispatcher is available.

    A queue that cannot be reached must not fail the request: the export is
    already recorded, and the failure is recorded with it, so the reader is
    told the archive will not arrive instead of waiting for it for ever.
    """

    enqueue = getattr(request.app.state, "enqueue_export", None)
    try:
        if callable(enqueue):
            enqueue(export_id)
            return
        from workers.tasks import build_domain_export

        build_domain_export.delay(export_id)
    except Exception as exc:
        logger.exception("unable to dispatch export %s", export_id)
        with get_session_factory(request)() as session:
            bundle = load_export(session, export_id)
            if bundle is not None:
                bundle.state = EXPORT_STATE_FAILED
                bundle.error = f"dispatch failed: {exc}"
                session.commit()


__all__ = ["complete_export", "fail_export", "router"]
