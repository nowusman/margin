from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from margin_agent.playbooks import discover_playbooks, discovery_roots, select_playbooks
from margin_db.charter import active_charter, charter_prompt_section
from margin_db.distillation import create_semantic_review_tasks
from margin_db.entity_catalog_candidates import select_relevant_candidates
from margin_db.extraction import ExtractionPersistenceService, ExtractionQualityReport
from margin_db.governance_extraction_review import create_governance_review_tasks
from margin_db.knowledge_models import ClaimEvidence, IdeaBlockEntity
from margin_db.reprocessing import finalize_reprocessing_diff, stale_knowledge_for_versions
from margin_db.source_models import SourceDocument, SourceDocumentVersion, SourceElement
from margin_ingest.contracts import contract_output, parse_contract_text
from margin_ingest.diagram_extraction import diagram_output
from margin_ingest.structurizr import parse_structurizr_dsl
from margin_schema.extraction import ExtractorOutput, primary_claim_evidence
from margin_schema.ontology import ONTOLOGY_VERSION
from margin_schema.text_normalization import normalize_statement
from workers.extract.accounting import (
    WindowExtractionResult,
    create_source_element_triage_tasks,
    fail_extraction_run,
    finish_extraction_run,
    prompt_hash,
    start_extraction_run,
)
from workers.extract.execution import DeterministicExtractor, execute_extraction
from workers.extract.inference import OpenAICompatibleExtractor
from workers.extract.markdown import DeterministicMarkdownExtractor
from workers.extract.prompts import EXTRACTOR_PROMPT_VERSION, EXTRACTOR_SYSTEM_PROMPT
from workers.extract.windows import ExtractionWindow, build_extraction_windows

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IdeaBlockPipelineResult:
    report: ExtractionQualityReport
    extraction_run_id: str
    stages: tuple[str, ...] = ("ingest", "extract", "distill")
    semantic_review_candidate_count: int = 0
    governance_review_candidate_count: int = 0


class MarginIdeaBlockPipeline:
    """Margin-native ingest, extraction, and review-candidate pipeline entry point."""

    def __init__(
        self,
        extractor: DeterministicMarkdownExtractor | None = None,
        persistence: ExtractionPersistenceService | None = None,
        inference_extractor: OpenAICompatibleExtractor | None = None,
        extraction_mode: str | None = None,
        max_window_tokens: int = 1_500,
        neighbor_tokens: int = 160,
        queue_embeddings: bool = False,
    ) -> None:
        self.extractor = extractor or DeterministicMarkdownExtractor()
        self._uses_default_extractor = extractor is None
        self.persistence = persistence or ExtractionPersistenceService()
        self.inference_extractor = inference_extractor
        self.extraction_mode = extraction_mode or ("llm" if inference_extractor is not None else "deterministic")
        self.max_window_tokens = max_window_tokens
        self.neighbor_tokens = neighbor_tokens
        self.queue_embeddings = queue_embeddings

    def run_once(
        self, session: Session, *, source_document_id: str, ingestion_job_id: str | None = None
    ) -> IdeaBlockPipelineResult:
        start = datetime.now(UTC)
        extractor_name = {
            "deterministic": "deterministic_markdown",
            "llm": "deterministic_markdown+openai_compatible_inference",
            "deterministic_llm": "deterministic_markdown+openai_compatible_inference",
        }.get(self.extraction_mode)
        if extractor_name is None:
            raise ValueError(f"unsupported extraction mode: {self.extraction_mode}")
        request_payload: dict[str, object] = {
            "source_document_id": source_document_id,
            "extractor": extractor_name,
            "mode": self.extraction_mode,
        }
        extraction_run = None
        try:
            source_document = session.get(SourceDocument, source_document_id)
            if source_document is None:
                raise ValueError(f"source document not found: {source_document_id}")
            version = session.scalar(
                select(SourceDocumentVersion)
                .where(
                    SourceDocumentVersion.source_document_id == source_document_id,
                    SourceDocumentVersion.status == "active",
                )
                .order_by(SourceDocumentVersion.created_at.desc(), SourceDocumentVersion.id.desc())
                .limit(1)
            )
            if version is None:
                raise ValueError(f"source document has no indexed version: {source_document_id}")
            superseded_version_ids = list(
                session.scalars(
                    select(SourceDocumentVersion.id).where(
                        SourceDocumentVersion.source_document_id == source_document_id,
                        SourceDocumentVersion.status == "superseded",
                    )
                ).all()
            )
            elements = list(
                session.scalars(
                    select(SourceElement)
                    .where(SourceElement.source_version_id == version.id)
                    .order_by(SourceElement.ordinal, SourceElement.id)
                ).all()
            )
            windows = build_extraction_windows(
                document=source_document,
                version=version,
                elements=elements,
                max_tokens=self.max_window_tokens,
                neighbor_tokens=self.neighbor_tokens,
            )
            windows = _with_governed_candidates(session, windows=windows, domain_id=source_document.domain_id)
            windows = _with_domain_charter(session, windows=windows, domain_id=source_document.domain_id)
            windows = _with_domain_playbooks(windows=windows, domain_id=source_document.domain_id)
            deterministic_extractor, deterministic_extractor_name = _deterministic_extractor_for(
                source_document, elements, fallback=self.extractor, use_specialized=self._uses_default_extractor
            )
            extractor_name = _run_extractor_name(
                self.extraction_mode, deterministic_extractor_name=deterministic_extractor_name
            )
            request_payload["extractor"] = extractor_name
            inference_extractor = self.inference_extractor
            model_id = inference_extractor.model if inference_extractor is not None else None
            extraction_prompt_hash = prompt_hash(EXTRACTOR_SYSTEM_PROMPT) if self.inference_extractor else None
            extraction_run = start_extraction_run(
                session,
                ingestion_job_id=ingestion_job_id,
                source_document_id=source_document_id,
                source_version_id=version.id,
                mode=self.extraction_mode,
                extractor=extractor_name,
                model_id=model_id,
                prompt_version=EXTRACTOR_PROMPT_VERSION if self.inference_extractor else None,
                prompt_hash=extraction_prompt_hash,
                expected_window_count=len(windows),
                metadata=_extraction_run_metadata(windows),
            )
            execution = execute_extraction(
                mode=self.extraction_mode,
                deterministic_extractor=deterministic_extractor,
                inference_extractor=inference_extractor,
                elements=elements,
                windows=windows,
                model_id=model_id,
                prompt_version=EXTRACTOR_PROMPT_VERSION if self.inference_extractor else None,
                prompt_hash=extraction_prompt_hash,
                deterministic_extractor_name=deterministic_extractor_name,
                residual_re_extraction=self.inference_extractor is not None,
            )
            report = self.persistence.persist_output(
                session,
                execution.output,
                source_document_id=source_document_id,
                force_candidate=False,
                extraction_run_id=extraction_run.id,
                model_id=model_id,
                prompt_version=EXTRACTOR_PROMPT_VERSION if self.inference_extractor else None,
                candidate_set_ids_by_block_key=_candidate_set_ids_by_block_key(execution.window_results),
            )
            governance_review_tasks = create_governance_review_tasks(
                session,
                candidates=execution.output.governance_records,
                source_document_id=source_document_id,
                domain_id=source_document.domain_id,
            )
            finish_extraction_run(
                session,
                run=extraction_run,
                source_document=source_document,
                results=execution.window_results,
                expected_source_element_ids={elem.id for elem in elements if (elem.text or "").strip()},
            )
            create_source_element_triage_tasks(session, source_version_id=version.id)
            finalize_reprocessing_diff(session, extraction_run.id)
            stale_knowledge_for_versions(session, superseded_version_ids)
            semantic_revision_ids = tuple(
                session.scalars(
                    select(ClaimEvidence.idea_block_revision_id)
                    .where(
                        ClaimEvidence.extraction_run_id == extraction_run.id,
                        ClaimEvidence.status == "active",
                        ClaimEvidence.idea_block_revision_id.is_not(None),
                    )
                    .distinct()
                    .order_by(ClaimEvidence.idea_block_revision_id)
                ).all()
            )
            semantic_candidates = create_semantic_review_tasks(
                session, domain_id=source_document.domain_id, idea_block_revision_ids=semantic_revision_ids
            )
            result = IdeaBlockPipelineResult(
                report=report,
                extraction_run_id=extraction_run.id,
                semantic_review_candidate_count=len(semantic_candidates),
                governance_review_candidate_count=len(governance_review_tasks),
            )
        except Exception as exc:
            source_document = session.get(SourceDocument, source_document_id)
            if extraction_run is not None and source_document is not None:
                fail_extraction_run(extraction_run, source_document, exc)
                session.flush()
            _log_pipeline_interaction(
                event="run_once", request_payload=request_payload, response_payload=None, start=start, error=str(exc)
            )
            raise
        _log_pipeline_interaction(
            event="run_once",
            request_payload={**request_payload, "element_count": len(elements)},
            response_payload={
                "report": report.to_dict(),
                "extraction_run_id": result.extraction_run_id,
                "stages": list(result.stages),
                "semantic_review_candidate_count": result.semantic_review_candidate_count,
                "governance_review_candidate_count": result.governance_review_candidate_count,
            },
            start=start,
        )
        return result


@dataclass(frozen=True)
class _PreparedDeterministicExtractor:
    output: ExtractorOutput

    def extract(self, *, elements: list[SourceElement]) -> ExtractorOutput:
        return self.output


def _deterministic_extractor_for(
    document: SourceDocument, elements: list[SourceElement], *, fallback: DeterministicExtractor, use_specialized: bool
) -> tuple[DeterministicExtractor, str]:
    if not use_specialized or not elements:
        return fallback, "deterministic_markdown"
    prepared = _specialized_deterministic_output(document, elements)
    if prepared is None:
        return fallback, "deterministic_markdown"
    output, extractor_name = prepared
    return _PreparedDeterministicExtractor(output), extractor_name


def _specialized_deterministic_output(
    document: SourceDocument, elements: list[SourceElement]
) -> tuple[ExtractorOutput, str] | None:
    ordered = sorted(
        elements,
        key=lambda elem: (
            elem.ordinal if elem.ordinal is not None else 2**31,
            elem.page_number if elem.page_number is not None else 2**31,
            elem.id,
        ),
    )
    text = "\n".join((elem.text or "") for elem in ordered)
    media_type = document.media_type.casefold()
    if media_type in {
        "application/json",
        "application/yaml",
        "application/x-yaml",
        "application/vnd.asyncapi",
        "application/vnd.oai.openapi",
        "text/yaml",
        "text/x-yaml",
    }:
        try:
            parsed = parse_contract_text(text, source_name=document.source_uri or document.title)
        except ValueError:
            return None
        output = contract_output(
            relative_path=document.source_uri or document.title,
            parsed=parsed,
            source_element_id=ordered[0].id,
            text=text,
        )
        return _remap_output_evidence(output, ordered), "deterministic_contract"
    diagram_types = {
        "text/vnd.structurizr": "structurizr",
        "text/vnd.mermaid": "mermaid",
        "text/vnd.plantuml": "plantuml",
    }
    diagram_type = diagram_types.get(media_type)
    if diagram_type is None:
        return None
    entities = ()
    relationships = ()
    if diagram_type == "structurizr":
        imported = parse_structurizr_dsl(text)
        entities = imported.entities
        relationships = imported.relationships
    output = diagram_output(
        relative_path=document.source_uri or document.title,
        diagram_type=diagram_type,
        source_element_id=ordered[0].id,
        entities=entities,
        relationships=relationships,
        markup=text,
    )
    return _remap_output_evidence(output, ordered), "deterministic_diagram"


def _remap_output_evidence(output: ExtractorOutput, elements: list[SourceElement]) -> ExtractorOutput:
    blocks = []
    for block in output.idea_blocks:
        primary = primary_claim_evidence(block)
        supporting_elem = next((elem for elem in elements if primary.source_quote in (elem.text or "")), None)
        if supporting_elem is None:
            raise ValueError("specialized deterministic output is not supported by a source element")
        blocks.append(
            block.model_copy(
                update={
                    "evidence": [
                        evidence.model_copy(update={"source_element_id": supporting_elem.id})
                        for evidence in block.evidence
                    ]
                }
            )
        )
    return ExtractorOutput(idea_blocks=blocks)


def _with_domain_charter(
    session: Session, *, windows: tuple[ExtractionWindow, ...], domain_id: str
) -> tuple[ExtractionWindow, ...]:
    """Attach the domain charter in force to each extraction window.

    The charter is scope guidance for the extractor. It is not evidence, and it
    never becomes a claim: it tells the extractor what the domain covers, and
    the records it produces are still judged against the source alone.
    """

    charter = active_charter(session, domain_id)
    if charter is None:
        return windows
    context = charter_prompt_section(charter)
    if not context:
        return windows
    return tuple(
        replace(window, domain_charter_version=charter.version, domain_charter_context=context) for window in windows
    )


def _extraction_run_metadata(windows: tuple[ExtractionWindow, ...]) -> dict[str, object]:
    """Describe what governed an extraction run.

    The playbooks that were consulted are recorded with their versions, so the
    influence of a playbook on the corpus can be traced back to the
    configuration that caused it.
    """

    return {
        "ontology_version": ONTOLOGY_VERSION,
        "domain_charter_version": next(
            (window.domain_charter_version for window in windows if window.domain_charter_version is not None), None
        ),
        "playbooks": [
            {"name": str(playbook.get("name")), "version": str(playbook.get("version"))}
            for window in windows
            for playbook in window.playbook_context
        ],
        "candidate_set_ids_by_window": {
            window.window_id: [
                str(candidate["canonical_id"])
                for candidate in window.governed_candidates
                if candidate.get("canonical_id")
            ]
            for window in windows
        },
    }


def _with_domain_playbooks(*, windows: tuple[ExtractionWindow, ...], domain_id: str) -> tuple[ExtractionWindow, ...]:
    """Attach the playbooks that govern a domain to each extraction window.

    A playbook is authored guidance about how a domain is read. It travels
    with the window so the extractor can apply it, and the playbooks that were
    consulted are recorded on the run, so their influence stays attributable.
    """

    playbooks = select_playbooks(discover_playbooks(discovery_roots()), domain_id=domain_id)
    if not playbooks:
        return windows
    context = tuple(
        {
            "name": playbook.name,
            "version": playbook.version,
            "description": playbook.description,
            "guidance": playbook.body,
        }
        for playbook in playbooks
    )
    return tuple(replace(window, playbook_context=context) for window in windows)


def _run_extractor_name(mode: str, *, deterministic_extractor_name: str) -> str:
    if mode in {"deterministic_llm", "llm"}:
        return f"{deterministic_extractor_name}+openai_compatible_inference"
    return deterministic_extractor_name


def _with_governed_candidates(
    session: Session, *, windows: tuple[ExtractionWindow, ...], domain_id: str
) -> tuple[ExtractionWindow, ...]:
    """Attach one bounded, reproducible candidate set to each extraction window."""

    enriched: list[ExtractionWindow] = []
    for window in windows:
        nearby_entity_ids = tuple(
            dict.fromkeys(
                session.scalars(
                    select(IdeaBlockEntity.entity_id)
                    .join(ClaimEvidence, ClaimEvidence.idea_block_id == IdeaBlockEntity.idea_block_id)
                    .where(ClaimEvidence.source_element_id.in_((window.source_element_id, *window.context_element_ids)))
                    .order_by(IdeaBlockEntity.entity_id)
                ).all()
            )
        )
        candidates = select_relevant_candidates(
            session,
            domain_id=domain_id,
            source_text=window.text,
            expected_ontology_types=_expected_ontology_types(window),
            nearby_entity_ids=nearby_entity_ids,
            limit=24,
        )
        enriched.append(
            replace(
                window,
                governed_candidates=tuple(candidate.prompt_payload() for candidate in candidates),
                ontology_version=ONTOLOGY_VERSION,
            )
        )
    return tuple(enriched)


def _expected_ontology_types(window: ExtractionWindow) -> tuple[str, ...]:
    values = window.document_metadata.get("expected_ontology_types")
    if not isinstance(values, list | tuple):
        return ()
    return tuple(value for value in values if isinstance(value, str))


def _candidate_set_ids_by_block_key(
    results: tuple[WindowExtractionResult, ...]
) -> dict[tuple[str, str], tuple[str, ...]]:
    """Keep the exact request candidate set available to persistence."""

    candidate_sets: dict[tuple[str, str], tuple[str, ...]] = {}
    for result in results:
        window = result.window
        candidate_ids = tuple(
            str(candidate["canonical_id"]) for candidate in window.governed_candidates if candidate.get("canonical_id")
        )
        for block in result.idea_blocks:
            key = (primary_claim_evidence(block).source_element_id, normalize_statement(block.statement))
            candidate_sets[key] = candidate_ids
    return candidate_sets


def _log_pipeline_interaction(
    *,
    event: str,
    request_payload: dict[str, object],
    response_payload: dict[str, object] | None,
    start: datetime,
    error: str | None = None,
) -> None:
    end = datetime.now(UTC)
    payload = {
        "event": event,
        "request": request_payload,
        "response": response_payload,
        "error": error,
        "start_timestamp": start.isoformat(),
        "end_timestamp": end.isoformat(),
        "duration_ms": round((end - start).total_seconds() * 1000, 3),
    }
    logger.info(
        "extraction_pipeline_interaction %s",
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        extra={"extraction_pipeline_interaction": payload},
    )
