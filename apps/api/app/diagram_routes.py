from __future__ import annotations

import os
import shlex
import subprocess

from fastapi import APIRouter, HTTPException, Request, Response

from app.catalog_helpers import require_source_read as _require_source_read
from app.source_routes import get_session_factory
from margin_db.diagrams import get_diagram_record, list_diagram_records

router = APIRouter(prefix="/api", tags=["diagrams"])


@router.get("/diagrams")
def list_diagrams(request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    with session_factory() as session:
        records = [
            record.summary()
            for record in list_diagram_records(session)
            if _can_read_diagram(session, request, record.source_document)
        ]
        return {"diagrams": records}


@router.get("/diagrams/{diagram_id}")
def get_diagram(diagram_id: str, request: Request) -> dict[str, object]:
    session_factory = get_session_factory(request)
    with session_factory() as session:
        record = get_diagram_record(session, diagram_id)
        if record is not None:
            _require_source_read(request, record.source_document, session)
            return record.detail()
    raise HTTPException(status_code=404, detail=f"diagram not found: {diagram_id}")


@router.get("/diagrams/{diagram_id}/render")
def render_diagram(diagram_id: str, request: Request) -> Response:
    session_factory = get_session_factory(request)
    with session_factory() as session:
        diagram = get_diagram_record(session, diagram_id)
        if diagram is None:
            raise HTTPException(status_code=404, detail=f"diagram not found: {diagram_id}")
        _require_source_read(request, diagram.source_document, session)
        diagram_type = diagram.type
        markup = diagram.markup
    if diagram_type == "plantuml":
        svg = _render_plantuml(markup)
    elif diagram_type == "structurizr":
        raise HTTPException(
            status_code=422,
            detail="Structurizr DSL is available as parsed uploaded source; server rendering is not enabled",
        )
    else:
        raise HTTPException(status_code=422, detail="Mermaid diagrams render in the browser with the Mermaid runtime")
    return Response(content=svg, media_type="image/svg+xml")


def _can_read_diagram(session: object, request: Request, source_document: object) -> bool:
    try:
        _require_source_read(request, source_document, session)
    except HTTPException:
        return False
    return True


def _render_plantuml(markup: str) -> str:
    command = os.getenv("MARGIN_PLANTUML_CMD", "plantuml").strip()
    if not command:
        raise HTTPException(status_code=503, detail="PlantUML renderer is not configured")
    try:
        result = subprocess.run(
            [*shlex.split(command), "-tsvg", "-pipe"], input=markup, text=True, capture_output=True, check=False
        )
    except OSError as exc:
        raise HTTPException(status_code=503, detail="PlantUML renderer is not available") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise HTTPException(status_code=503, detail=f"PlantUML renderer failed: {(result.stderr or '').strip()}")
    return result.stdout
