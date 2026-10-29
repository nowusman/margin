"""End-to-end tests for the evaluation routes.

These tests exercise the public evaluation API surface:
- ``GET  /api/evaluations/runs``
- ``GET  /api/evaluations/runs/{run_id}``
- ``POST /api/evaluations/runs``

The old ``POST /api/evaluations/knowledge-quality`` endpoint is verified to
be removed (it accepted caller-supplied pass counts).
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.main import create_app
from margin_db.base import Base
from margin_db.idea_block_resolution import create_idea_block_revision
from margin_db.knowledge_models import ClaimEvidence, IdeaBlock
from margin_db.source_models import (
    SourceDocument,
    SourceDocumentVersion,
    SourceElement,
    SourceElementOutcome,
    SourcePage,
)
from margin_db.user_models import Domain
from margin_eval.evaluation import EvaluationMode, run_evaluation
from margin_eval.gold_corpus import (
    GoldClaimExpectation,
    GoldCorpus,
    GoldEntityExpectation,
    GoldEvidenceAnchor,
    GoldFixture,
    load_gold_corpus,
)
from tests.helpers.catalog import catalog_entity


@pytest.fixture()
def client_with_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Create a test client backed by a sqlite engine and a real evaluation runner."""
    from sqlalchemy.pool import StaticPool

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    monkeypatch.setattr("app.quality_routes.RUNS_DIR", runs_dir)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    app = create_app()
    app.state.engine = engine
    from sqlalchemy.orm import sessionmaker

    app.state.session_factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return TestClient(app)


def _auth_headers() -> dict[str, str]:
    return {"x-margin-groups": "margin-admin"}


def _evaluation_engine(client: TestClient) -> Engine:
    app = cast(FastAPI, client.app)
    return cast(Engine, app.state.engine)


def _seed_evaluation_state(engine: Engine, fixture: GoldFixture) -> None:
    with Session(engine) as session:
        session.add(Domain(id="platform", name="Platform"))
        session.add(catalog_entity(entity_id="category-default", preferred_label="X", ontology_type="category"))
        session.flush()
        document = SourceDocument(
            id=f"doc-{fixture.fixture_id}",
            title=fixture.path.stem,
            source_uri=fixture.source_uri,
            logical_identity=fixture.source_uri or f"internal://platform/{fixture.fixture_id}",
            object_key=f"obj-{fixture.fixture_id}",
            media_type="text/markdown",
            sha256=fixture.fixture_id,
            domain_id=fixture.domain_id,
            category_id="category-default",
            sensitivity=fixture.sensitivity,
        )
        session.add(document)
        session.flush()
        version = SourceDocumentVersion(
            id=f"ver-{fixture.fixture_id}",
            source_document_id=document.id,
            version_sha256=fixture.fixture_id,
            status="active",
        )
        session.add(version)
        session.flush()
        for claim in fixture.claims:
            block_id = f"ib-{fixture.fixture_id}-{claim.id}"
            block = IdeaBlock(id=block_id, domain_id=fixture.domain_id)
            session.add(block)
            session.flush()
            revision = create_idea_block_revision(
                session,
                block,
                statement=claim.statement,
                normalized_statement=claim.statement,
                type=claim.claim_type,
                confidence="extracted",
                publication_status="verified",
                polarity=claim.polarity,
                qualifiers={},
                valid_from=None,
                valid_to=None,
                actor="test",
                process="deterministic",
            )
            page = SourcePage(
                id=f"page-{block.id}",
                source_version_id=version.id,
                page_number=1,
                content_checksum=f"c-{block.id}",
                classification="text_extracted",
            )
            session.add(page)
            session.flush()
            for idx, anchor in enumerate(claim.evidence_anchors):
                element = SourceElement(
                    id=f"elem_{block.id}_{idx}".replace("-", "_"),
                    source_version_id=version.id,
                    source_page_id=page.id,
                    native_element_id=f"native-{block.id}-{idx}",
                    kind="paragraph",
                    ordinal=idx,
                    text=anchor.quote,
                    checksum=f"c-{block.id}-{idx}",
                    page_number=1,
                )
                session.add(element)
                session.flush()
                evidence = ClaimEvidence(
                    id=f"ce-{block.id}-{idx}",
                    idea_block_id=block.id,
                    idea_block_revision_id=revision.id,
                    source_element_id=element.id,
                    source_quote=anchor.quote,
                    polarity=claim.polarity,
                    confidence="extracted",
                    extraction_method="deterministic",
                )
                session.add(evidence)
                outcome = SourceElementOutcome(
                    id=f"out-{block.id}-{idx}",
                    extraction_run_id=f"run-{fixture.fixture_id}",
                    source_element_id=element.id,
                    outcome="extracted",
                    extractor="deterministic",
                )
                session.add(outcome)
        session.commit()


@pytest.fixture()
def corpus_with_one_fixture(tmp_path: Path) -> GoldCorpus:
    fixture = GoldFixture(
        fixture_id="route-basic",
        document_class="adr",
        path=Path("tests/fixtures/gold_corpus/manifest.json"),
        domain_id="platform",
        category="adr",
        sensitivity="internal",
        source_uri="internal://platform/route-basic",
        claims=(
            GoldClaimExpectation(
                id="claim-route",
                statement="Route test claim.",
                claim_type="statement",
                evidence_anchors=(GoldEvidenceAnchor(quote="Route test claim."),),
            ),
        ),
        entities=(GoldEntityExpectation(name="Route Entity", kind="system"),),
    )
    return GoldCorpus(version="route-test-v1", fixtures=(fixture,), evaluation_modes=("deterministic",))


def test_post_evaluation_runs_creates_a_persisted_run(
    client_with_runner: TestClient, tmp_path: Path, corpus_with_one_fixture: GoldCorpus
) -> None:
    """The POST endpoint creates and persists a run for the full repository manifest."""
    engine = _evaluation_engine(client_with_runner)
    fixture = corpus_with_one_fixture.fixtures[0]
    _seed_evaluation_state(engine, fixture)
    # Use the real, full repository manifest so the loader's required-classes check passes.
    response = client_with_runner.post(
        "/api/evaluations/runs",
        json={"corpus_path": "tests/fixtures/gold_corpus/manifest.json"},
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["run"]["evaluation_mode"] == EvaluationMode.DETERMINISTIC.value
    assert body["run"]["status"] in {"passed", "failed"}
    assert body["run"]["fixture_count"] >= 1


def test_get_evaluation_runs_lists_persisted_runs(
    client_with_runner: TestClient, tmp_path: Path, corpus_with_one_fixture: GoldCorpus
) -> None:
    from app.quality_routes import RUNS_DIR

    engine = _evaluation_engine(client_with_runner)
    fixture = corpus_with_one_fixture.fixtures[0]
    _seed_evaluation_state(engine, fixture)
    with Session(engine) as session:
        run = run_evaluation(
            session, corpus_with_one_fixture, evaluation_mode=EvaluationMode.DETERMINISTIC, output_dir=RUNS_DIR
        )
    response = client_with_runner.get("/api/evaluations/runs", headers=_auth_headers())
    assert response.status_code == 200
    body = response.json()
    run_ids = [entry["id"] for entry in body["runs"]]
    assert run.run_id in run_ids


def test_get_evaluation_run_returns_full_details(
    client_with_runner: TestClient, tmp_path: Path, corpus_with_one_fixture: GoldCorpus
) -> None:
    from app.quality_routes import RUNS_DIR

    engine = _evaluation_engine(client_with_runner)
    fixture = corpus_with_one_fixture.fixtures[0]
    _seed_evaluation_state(engine, fixture)
    with Session(engine) as session:
        run = run_evaluation(
            session, corpus_with_one_fixture, evaluation_mode=EvaluationMode.DETERMINISTIC, output_dir=RUNS_DIR
        )
    response = client_with_runner.get(f"/api/evaluations/runs/{run.run_id}", headers=_auth_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["run"]["id"] == run.run_id
    metrics = body["run"]["metrics"]
    assert "claim_recall" in metrics
    assert "verified_evidence_coverage" in metrics
    assert "ghost_record_count" in metrics
    assert body["run"]["fixture_count"] == 1


def test_get_evaluation_run_404_for_missing_id(client_with_runner: TestClient) -> None:
    response = client_with_runner.get("/api/evaluations/runs/eval-missing", headers=_auth_headers())
    assert response.status_code == 404


def test_knowledge_quality_adjudication_endpoint_is_removed(client_with_runner: TestClient) -> None:
    """The adjudication endpoint that accepted caller counts is removed."""
    response = client_with_runner.post(
        "/api/evaluations/knowledge-quality",
        json={
            "expected_structured_fields": 10,
            "extracted_structured_fields": 9,
            "verified_relationship_true_positives": 9,
            "verified_relationship_false_positives": 1,
            "correct_entity_resolutions": 9,
            "total_entity_resolutions": 10,
        },
        headers=_auth_headers(),
    )
    assert response.status_code == 404


def test_post_evaluation_runs_rejects_missing_manifest(client_with_runner: TestClient) -> None:
    response = client_with_runner.post(
        "/api/evaluations/runs",
        json={"corpus_path": "tests/fixtures/gold_corpus/does-not-exist.json"},
        headers=_auth_headers(),
    )
    assert response.status_code == 400


def test_post_evaluation_runs_rejects_invalid_mode(client_with_runner: TestClient) -> None:
    response = client_with_runner.post(
        "/api/evaluations/runs", json={"evaluation_mode": "invalid_mode"}, headers=_auth_headers()
    )
    assert response.status_code == 400


def test_run_detail_links_metrics_to_database_ids(
    client_with_runner: TestClient, tmp_path: Path, corpus_with_one_fixture: GoldCorpus
) -> None:
    """Every metric detail must include a database ID for the source element."""
    from app.quality_routes import RUNS_DIR

    engine = _evaluation_engine(client_with_runner)
    fixture = corpus_with_one_fixture.fixtures[0]
    _seed_evaluation_state(engine, fixture)
    with Session(engine) as session:
        run = run_evaluation(
            session, corpus_with_one_fixture, evaluation_mode=EvaluationMode.DETERMINISTIC, output_dir=RUNS_DIR
        )
    response = client_with_runner.get(f"/api/evaluations/runs/{run.run_id}", headers=_auth_headers())
    body = response.json()
    true_positives = body["run"]["true_positive_evidence"][0]
    claim_true_positives = true_positives["claim_true_positives"]
    assert claim_true_positives, "expected at least one claim true positive"
    for tp in claim_true_positives:
        assert tp["actual_ids"]
        assert tp["evidence_ids"]


def test_run_detail_records_evaluation_mode(
    client_with_runner: TestClient, tmp_path: Path, corpus_with_one_fixture: GoldCorpus
) -> None:
    from app.quality_routes import RUNS_DIR

    engine = _evaluation_engine(client_with_runner)
    fixture = corpus_with_one_fixture.fixtures[0]
    _seed_evaluation_state(engine, fixture)
    with Session(engine) as session:
        run = run_evaluation(
            session, corpus_with_one_fixture, evaluation_mode=EvaluationMode.OPERATIONAL, output_dir=RUNS_DIR
        )
    response = client_with_runner.get(f"/api/evaluations/runs/{run.run_id}", headers=_auth_headers())
    body = response.json()
    assert body["run"]["evaluation_mode"] == EvaluationMode.OPERATIONAL.value
    assert "active_extraction_model_id" in body["run"]


def test_repository_gold_corpus_loads_through_public_loader() -> None:
    corpus = load_gold_corpus()
    assert len(corpus.fixtures) >= 12
    assert corpus.version.startswith("margin-knowledge-gold")
