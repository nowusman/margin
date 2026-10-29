from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.admin_routes import router as admin_router
from app.agent_routes import router as agent_router
from app.assistant_routes import router as assistant_router
from app.catalog_misc_routes import router as catalog_misc_router
from app.catalog_routes import router as catalog_router
from app.charter_routes import router as charter_router
from app.config import get_settings
from app.dashboard_routes import router as dashboard_router
from app.diagram_routes import router as diagram_router
from app.entity_resolution_routes import router as entity_resolution_router, source_entity_router
from app.export_routes import router as export_router
from app.extraction_routes import router as extraction_router
from app.governance_routes import router as governance_router
from app.graph_routes import router as graph_router
from app.narrative_routes import router as narrative_router
from app.personal_routes import router as personal_router
from app.quality_routes import router as quality_router
from app.rbac import rbac_middleware
from app.relationship_routes import router as relationship_router
from app.source_batch_routes import router as source_batch_router
from app.source_routes import router as source_router
from app.user_routes import router as user_router
from margin_ingest.converters import validate_converter_dependencies


def create_app() -> FastAPI:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            validate_converter_dependencies()
            app.state.converter_dependency_status = "ready"
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="Margin API",
        version="0.3.0",
        description="Canonical knowledge ingestion, search, and governance API.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.retrieval_service = None
    app.state.retrieval_service_error = ""
    app.middleware("http")(rbac_middleware)
    app.include_router(admin_router)
    app.include_router(user_router)
    app.include_router(agent_router)
    app.include_router(assistant_router)
    app.include_router(dashboard_router)
    app.include_router(entity_resolution_router)
    app.include_router(source_entity_router)
    app.include_router(relationship_router)
    app.include_router(graph_router)
    app.include_router(charter_router)
    app.include_router(narrative_router)
    app.include_router(personal_router)
    app.include_router(export_router)
    app.include_router(catalog_router)
    app.include_router(catalog_misc_router)
    app.include_router(diagram_router)
    app.include_router(extraction_router)
    app.include_router(governance_router)
    app.include_router(quality_router)
    app.include_router(source_batch_router)
    app.include_router(source_router)

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "margin-api"}

    @app.get("/ready", tags=["system"])
    def ready() -> dict[str, object]:
        converter_status = getattr(app.state, "converter_dependency_status", "unknown")
        database_status = "unavailable"
        session_factory = getattr(app.state, "session_factory", None)
        if session_factory is not None:
            try:
                with session_factory() as session:
                    session.execute(text("SELECT 1"))
                database_status = "ready"
            except (OSError, SQLAlchemyError):
                database_status = "unavailable"
        ready = database_status == "ready" and converter_status == "ready"
        payload = {
            "status": "ready" if ready else "not_ready",
            "environment": settings.env,
            "system_of_record": "postgresql",
            "object_store_bucket": settings.object_store_bucket,
            "release_status": "ready" if ready else "blocked",
            "database": database_status,
            "converter_dependencies": converter_status,
        }
        if not ready:
            raise HTTPException(status_code=503, detail=payload)
        return payload

    def _rbac_payload(request: Request) -> dict[str, object]:
        permissions = request.state.permissions
        return {
            "principal_id": request.state.principal_id,
            "auth_source": getattr(request.state, "auth_source", "unknown"),
            "group_ids": list(permissions.group_ids),
            "domain_permissions": {key: sorted(value) for key, value in permissions.domain_permissions.items()},
            "category_permissions": {key: sorted(value) for key, value in permissions.category_permissions.items()},
            "sensitivity_permissions": {
                key: sorted(value) for key, value in permissions.sensitivity_permissions.items()
            },
            "authz_backend": permissions.authz_backend,
        }

    @app.get("/rbac/me", tags=["system"])
    def rbac_me(request: Request) -> dict[str, object]:
        return _rbac_payload(request)

    @app.get("/api/rbac/me", tags=["system"])
    def api_rbac_me(request: Request) -> dict[str, object]:
        return _rbac_payload(request)

    return app


app = create_app()
