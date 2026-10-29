from __future__ import annotations

from types import SimpleNamespace

import pytest

from margin_db.embeddings import enqueue_embedding_job, process_embedding_jobs
from margin_embedding.interfaces import EMBEDDING_DIMENSIONS, validate_embedding_batch
from margin_embedding.text import embedding_text_for_idea_block


class _Result:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows

    def all(self) -> list[object]:
        return self.rows


class _Session:
    def __init__(self, jobs: list[object], revision: object) -> None:
        self.jobs = jobs
        self.revision = revision
        self.added: list[object] = []
        self.scalar_calls = 0
        self.scalars_calls = 0

    def scalars(self, query: object) -> _Result:
        del query
        self.scalars_calls += 1
        return _Result(self.jobs if self.scalars_calls == 1 else [])

    def get(self, model: object, target_id: str) -> object:
        del model, target_id
        return self.revision

    def scalar(self, query: object) -> None:
        del query
        self.scalar_calls += 1
        return None

    def add(self, value: object) -> None:
        self.added.append(value)

    def flush(self) -> None:
        return None


class _BatchProvider:
    provider_id = "test-provider"
    model_id = "test-model"
    index_version = "test-index"
    dimensions = EMBEDDING_DIMENSIONS

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def embed(self, texts: tuple[str, ...]) -> tuple[list[float], ...]:
        self.calls.append(texts)
        return tuple([0.0] * EMBEDDING_DIMENSIONS for _ in texts)


def test_embedding_text_builder_preserves_revision_semantics_for_non_search_uses() -> None:
    revision = SimpleNamespace(
        normalized_statement="normalized policy statement",
        statement="full source span must not be embedded",
        type="policy",
        polarity="refutes",
        qualifiers={"version": "v2"},
    )

    text = embedding_text_for_idea_block(revision, entity_ids=("entity-z", "entity-a"))

    assert "normalized policy statement" in text
    assert "policy" in text
    assert "refutes" in text
    assert '"version":"v2"' in text
    assert "entity-a,entity-z" in text
    assert "full source span must not be embedded" not in text


def test_embedding_target_validation_rejects_source_document() -> None:
    with pytest.raises(ValueError, match="allowed targets"):
        enqueue_embedding_job(
            object(),  # type: ignore[arg-type]
            target_type="source_document",  # type: ignore[arg-type]
            target_id="document-1",
            model_id="model-1",
            index_version="index-1",
            reason="test",
        )


def test_embedding_batch_processing_uses_one_call_and_fixed_dimension() -> None:
    jobs = [
        SimpleNamespace(
            id="job-1",
            target_type="idea_block_revision",
            target_id="revision-1",
            model_id="test-model",
            index_version="test-index",
            status="queued",
            metadata_={},
            error_message=None,
        ),
        SimpleNamespace(
            id="job-2",
            target_type="idea_block_revision",
            target_id="revision-2",
            model_id="test-model",
            index_version="test-index",
            status="queued",
            metadata_={},
            error_message=None,
        ),
    ]
    revision = SimpleNamespace(
        id="revision-1", idea_block_id="block-1", search_text="normalized statement fact supports"
    )
    session = _Session(jobs, revision)
    provider = _BatchProvider()

    completed = process_embedding_jobs(session, provider=provider)

    assert completed == 2
    assert len(provider.calls) == 1
    assert provider.calls[0] == ("normalized statement fact supports", "normalized statement fact supports")
    assert {job.status for job in jobs} == {"completed"}
    assert len(session.added) == 2


def test_embedding_validation_rejects_non_approved_dimension() -> None:
    with pytest.raises(ValueError, match="exactly|requires 384"):
        validate_embedding_batch((list(range(383)),), expected_count=1, declared_dimensions=EMBEDDING_DIMENSIONS)

    with pytest.raises(ValueError, match="requires 384"):
        validate_embedding_batch(([0.0] * EMBEDDING_DIMENSIONS,), expected_count=1, declared_dimensions=383)
