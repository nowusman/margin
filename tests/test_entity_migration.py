from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pytest import MonkeyPatch
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


def test_postgresql_status_check_deparse_is_recognized() -> None:
    migration_path = Path("packages/margin_db/alembic/versions/0013_unified_job_lifecycle.py")
    spec = importlib.util.spec_from_file_location("unified_job_lifecycle", migration_path)
    assert spec is not None
    assert spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    postgresql_sqltext = (
        "CHECK ((status = ANY (ARRAY['received'::text, 'converting'::text, 'extracting'::text, "
        "'resolving'::text, 'embedding'::text, 'review_required'::text, 'completed'::text, "
        "'failed'::text, 'quarantined'::text])))"
    )

    assert migration._matches_status_check(postgresql_sqltext)
    assert not migration._matches_status_check(postgresql_sqltext.replace("'quarantined'", "'queued'"))
    assert not migration._matches_status_check(postgresql_sqltext.replace("= ANY", "<> ALL"))


def test_clean_bootstrap_reaches_the_unified_ingestion_lifecycle(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    database_path = tmp_path / "entity-cutover.sqlite"
    monkeypatch.delenv("MARGIN_DATABASE_URL", raising=False)
    config = Config("packages/margin_db/alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite+pysqlite:///{database_path.as_posix()}")
    try:
        table_names = set(inspect(engine).get_table_names())
        assert {
            "entities",
            "entity_aliases",
            "entity_mentions",
            "entity_resolution_decisions",
            "entity_resolution_actions",
            "entity_reresolution_jobs",
            "entity_relationships",
            "graph_insight_decisions",
        } <= table_names
        assert "graph_insight_dismissals" not in table_names
        for table_name in ("extraction_runs", "embedding_jobs"):
            assert "ingestion_job_id" in {column["name"] for column in inspect(engine).get_columns(table_name)}
        assert (
            not {
                "architecture_entities",
                "architecture_entity_sources",
                "controlled_vocabulary",
                "vocabulary_aliases",
                "glossary_terms",
                "glossary_aliases",
            }
            & table_names
        )
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0023_ingestion_job_sequence"
            )
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO ingestion_jobs "
                        "(id, status, retry_count, max_retries, metadata, created_at, updated_at) "
                        "VALUES ('bad', 'queued', 0, 3, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    )
                )
    finally:
        engine.dispose()
