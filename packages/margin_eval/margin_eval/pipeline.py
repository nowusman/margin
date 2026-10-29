"""End-to-end evaluation pipeline that ingests fixtures through the production stack.

This module implements the high-level "isolated scope + production pipeline"
entry point required by the evaluation contract.  It is a thin wrapper over
``run_evaluation`` that owns the lifecycle of one evaluation run:

1. Create a fresh in-memory database (isolated scope).
2. For each fixture, ingest the source file through ``SourceIngestionService``.
3. Run the deterministic extraction pipeline (``MarginIdeaBlockPipeline``).
4. Run ``run_evaluation`` against the populated database.
5. Aggregate the per-fixture results into one ``EvaluationRun``.

The pipeline is intentionally kept narrow.  All real work (matching,
metrics, gates) happens inside ``evaluation.py``.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from margin_db.base import Base
from margin_eval.evaluation import EvaluationMode, EvaluationRun, run_evaluation
from margin_eval.gold_corpus import GoldCorpus, GoldFixture


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Result of a full evaluation pipeline run.

    Attributes
    ----------
    run:
        The aggregated ``EvaluationRun`` with per-fixture results.
    engine:
        The SQLAlchemy engine that owns the isolated database scope.
        Callers are responsible for disposing it.
    """

    run: EvaluationRun
    engine: Engine


def run_evaluation_pipeline(
    corpus: GoldCorpus,
    *,
    evaluation_mode: EvaluationMode = EvaluationMode.DETERMINISTIC,
    model_id: str | None = None,
    prompt_version: str | None = None,
    index_version: str | None = None,
    output_dir: Path = Path(".margin/evaluation_runs"),
    object_store_root: Path | None = None,
) -> PipelineResult:
    """Run the full evaluation pipeline in an isolated database scope.

    Parameters
    ----------
    corpus:
        Gold corpus to evaluate.
    evaluation_mode:
        ``deterministic`` (default) or ``operational``.
    model_id / prompt_version / index_version:
        Recorded in the persisted run payload and used to derive the run id.
    output_dir:
        Directory where the run JSON is written.
    object_store_root:
        Filesystem root for the object store.  A temporary directory is
        created automatically when not provided.

    The corpus is ingested and extracted through the production path, because a
    gate that measures a precomputed corpus measures nothing about the system
    that ships.
    """

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    object_root = object_store_root or Path(tempfile.mkdtemp(prefix="margin-eval-"))
    object_root.mkdir(parents=True, exist_ok=True)
    with session_factory() as session:
        _seed_corpus_catalogue(session, corpus)
        session.commit()
    for fixture in corpus.fixtures:
        with session_factory() as session:
            uploaded = _ingest_fixture_in_scope(session, fixture, object_store_root=object_root)
            _extract_fixture_in_scope(session, uploaded, object_store_root=object_root)
            session.commit()
    with session_factory() as session:
        run = run_evaluation(
            session,
            corpus,
            evaluation_mode=evaluation_mode,
            model_id=model_id,
            prompt_version=prompt_version,
            index_version=index_version,
            output_dir=output_dir,
        )
    return PipelineResult(run=run, engine=engine)


def _seed_corpus_catalogue(session: Session, corpus: GoldCorpus) -> int:
    """Seed the catalogue with the records the corpus declares.

    Margin never creates a canonical record from extraction: a mention that names
    a record the catalogue does not hold is unresolved, and the relationship it
    carries is quarantined for review. A corpus measured against an empty
    catalogue therefore never yields an edge, which measures the empty catalogue
    rather than the surfaces. The corpus declares the records its documents refer
    to, so the harness seeds them first, the way a deployment's catalogue already
    holds them.

    The gate still measures resolution: a seeded record only counts when the
    extraction resolves the mention to it.
    """

    from margin_db.entity_catalog import create_entity
    from margin_db.entity_models import Entity
    from margin_db.user_models import Domain
    from margin_schema.ontology import normalize_entity_kind
    from margin_schema.text_normalization import normalize_entity_name

    seeded = 0
    for fixture in corpus.fixtures:
        if session.get(Domain, fixture.domain_id) is None:
            session.add(Domain(id=fixture.domain_id, name=fixture.domain_id.title()))
        for entity in fixture.entities:
            kind = normalize_entity_kind(entity.kind)
            if kind is None:
                continue
            existing = session.scalar(
                select(Entity).where(
                    Entity.domain_id == fixture.domain_id, Entity.normalized_label == normalize_entity_name(entity.name)
                )
            )
            if existing is not None:
                continue
            create_entity(session, preferred_label=entity.name, ontology_type=kind, domain_id=fixture.domain_id)
            seeded += 1
    session.flush()
    return seeded


def _ingest_fixture_in_scope(session: Session, fixture: GoldFixture, *, object_store_root: Path) -> Any:
    """Ingest a fixture file through ``SourceIngestionService``.

    This keeps the import surface small by importing the heavy
    source-ingestion stack lazily — the pure matcher never needs it.
    """
    from margin_db.entity_catalog import create_entity
    from margin_db.entity_models import Entity
    from margin_db.object_storage import FilesystemObjectStore
    from margin_db.source_ingestion import SourceIngestionService, WorkerDocumentConverter
    from margin_db.user_models import Domain

    if session.get(Domain, fixture.domain_id) is None:
        session.add(Domain(id=fixture.domain_id, name=fixture.domain_id.title()))
    if (
        session.scalar(
            select(Entity).where(
                Entity.ontology_type == "category",
                Entity.normalized_label == "design document",
                Entity.lifecycle_status == "active",
            )
        )
        is None
    ):
        create_entity(
            session,
            preferred_label="Design Document",
            ontology_type="category",
            domain_id=None,
            lifecycle_status="active",
            reason="evaluation fixture category",
        )
    session.flush()

    object_store = FilesystemObjectStore(object_store_root)
    converter = WorkerDocumentConverter()
    service = SourceIngestionService(object_store, converter=converter)
    file_bytes = fixture.path.read_bytes()
    media_type = _media_type_for(fixture.path)
    source_uri = fixture.source_identity()
    return service.upload_source(
        session,
        filename=fixture.path.name,
        media_type=media_type,
        data=file_bytes,
        source_uri=source_uri,
        domain_id=fixture.domain_id,
        category="Design Document",
        sensitivity=fixture.sensitivity,
    )


def _extract_fixture_in_scope(session: Session, uploaded: Any, *, object_store_root: Path) -> None:
    """Convert, index and extract an ingested fixture through production code.

    The gate must measure the extraction that production performs. A corpus that
    was merely uploaded holds no claims, no entities and no relationships, so
    every surface measured over it would be empty.
    """

    from margin_db.object_storage import FilesystemObjectStore
    from margin_db.source_ingestion import SourceIngestionService, WorkerDocumentConverter
    from margin_db.source_models import INGESTION_JOB_FAILED
    from workers.extract.pipeline import MarginIdeaBlockPipeline

    service = SourceIngestionService(FilesystemObjectStore(object_store_root), converter=WorkerDocumentConverter())
    if uploaded.ingestion_job is None:
        return
    job = service.process_job(session, uploaded.ingestion_job.id)
    if job.status == INGESTION_JOB_FAILED:
        # The fixture was not ingested. The evaluation reports the missing
        # records; the gate must not crash on a document it could not read.
        return
    MarginIdeaBlockPipeline().run_once(session, source_document_id=uploaded.source_document.id)


def _media_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".yaml": "application/yaml",
        ".yml": "application/yaml",
        ".json": "application/json",
        ".dsl": "text/vnd.structurizr",
        ".mmd": "text/vnd.mermaid",
        ".puml": "text/vnd.plantuml",
    }.get(suffix, "application/octet-stream")


__all__ = ["PipelineResult", "run_evaluation_pipeline"]
