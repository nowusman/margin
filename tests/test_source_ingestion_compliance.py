from __future__ import annotations

from pathlib import Path

from app.main import create_app


def test_required_source_ingestion_files_exist() -> None:
    required_paths = [
        "apps/api/app/source_routes.py",
        "packages/margin_db/alembic/versions/0001_initial_schema.py",
        "packages/margin_db/margin_db/source_ingestion.py",
        "packages/margin_ingest/margin_ingest/converters.py",
        "workers/ingest/metadata.py",
        "packages/margin_ingest/margin_ingest/indexing.py",
        "packages/margin_ingest/margin_ingest/quality.py",
        "packages/margin_ingest/margin_ingest/contracts.py",
    ]

    for path in required_paths:
        assert Path(path).exists(), path


def test_api_routes_are_mounted_on_application() -> None:
    route_paths = {route.path for route in create_app().routes}

    assert "/api/sources" in route_paths
    assert "/api/sources/{source_document_id}/preview" in route_paths
    assert "/api/admin/sources/{source_document_id}/metadata" in route_paths
    assert "/api/admin/ingestion-jobs" in route_paths
    assert "/api/admin/ingestion-jobs/{job_id}/retry" in route_paths


def test_fixtures_cover_required_upload_and_parser_formats() -> None:
    fixtures = {path.name for path in Path("tests/fixtures/source_ingestion").iterdir()}

    assert {
        "minimal.pdf",
        "minimal.docx",
        "minimal.pptx",
        "architecture.md",
        "note.txt",
        "system.dsl",
        "flow.mmd",
        "deployment.puml",
        "service.openapi.yaml",
        "events.asyncapi.yaml",
    }.issubset(fixtures)
