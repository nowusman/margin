from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from margin_db.source_models import SourceElement
from margin_schema.extraction import (
    ExtractedEntity,
    ExtractedGovernanceRecordCandidate,
    ExtractedIdeaBlock,
    ExtractedRelationship,
    ExtractorOutput,
    primary_claim_evidence,
)
from margin_schema.text_normalization import normalize_entity_name, normalize_statement, tokenize_search
from workers.extract.accounting import WindowExtractionResult
from workers.extract.inference import ExtractionRequest, InferenceResult, OpenAICompatibleExtractor
from workers.extract.windows import ExtractionWindow


class DeterministicExtractor(Protocol):
    def extract(self, *, elements: list[SourceElement]) -> ExtractorOutput: ...


@dataclass(frozen=True)
class ExtractionExecution:
    output: ExtractorOutput
    window_results: tuple[WindowExtractionResult, ...]


def execute_extraction(
    *,
    mode: str,
    deterministic_extractor: DeterministicExtractor,
    inference_extractor: OpenAICompatibleExtractor | None,
    elements: list[SourceElement],
    windows: tuple[ExtractionWindow, ...],
    model_id: str | None,
    prompt_version: str | None,
    prompt_hash: str | None,
    deterministic_extractor_name: str = "deterministic_markdown",
    residual_re_extraction: bool = False,
) -> ExtractionExecution:
    if mode not in {"deterministic", "deterministic_llm", "llm"}:
        raise ValueError(f"unsupported extraction mode: {mode}")
    if mode in {"deterministic_llm", "llm"} and inference_extractor is None:
        raise ValueError(f"{mode} extraction requires an inference extractor")

    blocks_by_key: dict[tuple[str, str], ExtractedIdeaBlock] = {}
    owner_by_key: dict[tuple[str, str], str] = {}
    governance_records_by_window: dict[str, list[ExtractedGovernanceRecordCandidate]] = {
        window.window_id: [] for window in windows
    }
    duplicate_window_ids: set[str] = set()
    deterministic_covered_window_ids: set[str] = set()
    inference_by_window: dict[str, InferenceResult] = {}
    deterministic_blocks_by_window: dict[str, tuple[ExtractedIdeaBlock, ...]] = {
        window.window_id: () for window in windows
    }
    deterministic = deterministic_extractor.extract(elements=elements)
    windows_by_element: dict[str, list[ExtractionWindow]] = {}
    for window in windows:
        for element_id in (window.source_element_id, *window.record_element_ids):
            windows_by_element.setdefault(element_id, []).append(window)
    for block in deterministic.idea_blocks:
        evidence = primary_claim_evidence(block)
        key = _block_key(block)
        owner = _deterministic_owner(block, evidence.source_element_id, windows_by_element)
        if owner is None:
            raise ValueError(f"deterministic extractor cited unknown source element {evidence.source_element_id}")
        blocks_by_key[key] = block
        owner_by_key[key] = owner.window_id
        deterministic_blocks_by_window[owner.window_id] = (*deterministic_blocks_by_window[owner.window_id], block)
    for candidate in deterministic.governance_records:
        owner = _deterministic_governance_owner(candidate, windows_by_element)
        if owner is None:
            raise ValueError(
                "deterministic extractor cited an unknown source element for a governance record candidate"
            )
        governance_records_by_window[owner.window_id].append(candidate)

    if mode in {"deterministic_llm", "llm"}:
        assert inference_extractor is not None
        inference_requests: list[ExtractionRequest] = []
        for window in windows:
            residual_text = window.text
            residual_text = _residual_text(window, deterministic_blocks_by_window.get(window.window_id, ()))
            if not residual_text:
                deterministic_covered_window_ids.add(window.window_id)
                continue
            inference_requests.append(_request(window, text=residual_text))
        inference_results = inference_extractor.extract_many(tuple(inference_requests))
        if len(inference_results) != len(inference_requests):
            raise RuntimeError("inference extractor did not return one result per extraction window")
        inference_by_window = {result.request.window_id: result for result in inference_results}
        for result in inference_results:
            if result.output is None:
                continue
            governance_records_by_window[result.request.window_id].extend(result.output.governance_records)
            for raw_block in result.output.idea_blocks:
                block = raw_block.model_copy(update={"extraction_method": "openai_compatible_inference"})
                key = _block_key(block)
                existing = blocks_by_key.get(key)
                if existing is None:
                    blocks_by_key[key] = block
                    owner_by_key[key] = result.request.window_id
                    continue
                blocks_by_key[key] = _merge_blocks(existing, block)
                duplicate_window_ids.add(result.request.window_id)

    residual_window_ids: set[str] = set()
    if residual_re_extraction and mode in {"deterministic_llm", "llm"}:
        assert inference_extractor is not None
        uncovered_windows = tuple(
            window
            for window in windows
            if window.window_id not in owner_by_key.values()
            and _is_residual_candidate(window.window_id, inference_by_window)
        )
        if uncovered_windows:
            residual_window_ids = _residual_re_extraction(
                uncovered_windows=uncovered_windows,
                inference_extractor=inference_extractor,
                blocks_by_key=blocks_by_key,
                owner_by_key=owner_by_key,
                governance_records_by_window=governance_records_by_window,
                inference_by_window=inference_by_window,
                duplicate_window_ids=duplicate_window_ids,
            )

    blocks_by_window: dict[str, list[ExtractedIdeaBlock]] = {window.window_id: [] for window in windows}
    for key, block in blocks_by_key.items():
        blocks_by_window[owner_by_key[key]].append(block)

    window_results = tuple(
        _window_result(
            mode=mode,
            window=window,
            blocks=tuple(blocks_by_window[window.window_id]),
            governance_records=tuple(governance_records_by_window[window.window_id]),
            inference_result=inference_by_window.get(window.window_id),
            duplicate=(
                window.window_id in duplicate_window_ids
                or (
                    mode in {"deterministic_llm", "llm"}
                    and window.window_id in deterministic_covered_window_ids
                    and not blocks_by_window[window.window_id]
                )
            ),
            residual=window.window_id in residual_window_ids,
            model_id=model_id,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
            deterministic_extractor_name=deterministic_extractor_name,
        )
        for window in windows
    )
    ordered_blocks = tuple(
        block
        for window in windows
        for block in sorted(
            blocks_by_window[window.window_id], key=lambda candidate: (_block_key(candidate), candidate.statement)
        )
    )
    ordered_governance_records = tuple(
        candidate for window in windows for candidate in governance_records_by_window[window.window_id]
    )
    return ExtractionExecution(
        output=ExtractorOutput(idea_blocks=list(ordered_blocks), governance_records=list(ordered_governance_records)),
        window_results=window_results,
    )


def _request(window: ExtractionWindow, *, text: str | None = None) -> ExtractionRequest:
    return ExtractionRequest(
        window_id=window.window_id,
        source_element_id=window.source_element_id,
        text=window.text if text is None else text,
        content_type=window.content_type,
        source_page_id=window.source_page_id,
        page_number=window.page_number,
        section_heading=window.section_heading,
        section_path=window.section_path,
        preceding_context=tuple(vars(item) for item in window.preceding_context),
        following_context=tuple(vars(item) for item in window.following_context),
        document_metadata=window.document_metadata,
        window_strategy=window.strategy,
        primary_source_element_ids=window.primary_source_element_ids,
        eligible_citable_elements=tuple(vars(item) for item in window.eligible_citable_elements),
        governed_candidates=window.governed_candidates,
        ontology_version=window.ontology_version,
        domain_charter_version=window.domain_charter_version,
        domain_charter_context=window.domain_charter_context,
        playbook_context=window.playbook_context,
    )


def _window_result(
    *,
    mode: str,
    window: ExtractionWindow,
    blocks: tuple[ExtractedIdeaBlock, ...],
    governance_records: tuple[ExtractedGovernanceRecordCandidate, ...],
    inference_result: InferenceResult | None,
    duplicate: bool,
    residual: bool,
    model_id: str | None,
    prompt_version: str | None,
    prompt_hash: str | None,
    deterministic_extractor_name: str,
) -> WindowExtractionResult:
    extractor = {
        "deterministic": deterministic_extractor_name,
        "llm": f"{deterministic_extractor_name}+openai_compatible_inference",
        "deterministic_llm": f"{deterministic_extractor_name}+openai_compatible_inference",
    }[mode]
    quarantined_count = sum(len(block.unmapped) for block in blocks)
    warnings = list(inference_result.warnings if inference_result is not None else ())
    if quarantined_count:
        warnings.append(f"{quarantined_count} relationship(s) quarantined for ontology review")
    if inference_result is not None and not inference_result.succeeded:
        outcome = "failed"
    elif quarantined_count:
        outcome = "unsupported"
    elif blocks or governance_records:
        outcome = "extracted"
    elif duplicate:
        outcome = "duplicate"
    else:
        outcome = "no_claim"
    return WindowExtractionResult(
        window=window,
        outcome=outcome,
        extractor=extractor,
        idea_blocks=blocks,
        governance_records=governance_records,
        model_id=model_id,
        prompt_version=prompt_version,
        prompt_hash=prompt_hash,
        attempt_count=inference_result.attempt_count if inference_result is not None else 1,
        input_tokens=inference_result.input_tokens if inference_result is not None else 0,
        output_tokens=inference_result.output_tokens if inference_result is not None else 0,
        warnings=tuple(warnings),
        error=inference_result.error if inference_result is not None else None,
        residual_re_extraction=residual,
    )


def _is_residual_candidate(window_id: str, inference_by_window: dict[str, InferenceResult]) -> bool:
    result = inference_by_window.get(window_id)
    if result is None:
        return True
    if not result.succeeded:
        return False
    return result.output is None or not (result.output.idea_blocks or result.output.governance_records)


def _residual_re_extraction(
    *,
    uncovered_windows: tuple[ExtractionWindow, ...],
    inference_extractor: OpenAICompatibleExtractor,
    blocks_by_key: dict[tuple[str, str], ExtractedIdeaBlock],
    owner_by_key: dict[tuple[str, str], str],
    governance_records_by_window: dict[str, list[ExtractedGovernanceRecordCandidate]],
    inference_by_window: dict[str, InferenceResult],
    duplicate_window_ids: set[str],
) -> set[str]:
    """Retry uncovered windows once more with full window text.

    A window is uncovered when neither the deterministic extractor nor the
    first inference pass produced a block for it. This pass gives high-value
    residual content a final targeted extraction attempt and merges the
    accounting into the existing per-window results.
    """
    retry_results = inference_extractor.extract_many(tuple(_request(window) for window in uncovered_windows))
    retried_window_ids: set[str] = set()
    for result in retry_results:
        retried_window_ids.add(result.request.window_id)
        first = inference_by_window.get(result.request.window_id)
        inference_by_window[result.request.window_id] = (
            _combine_inference_results(first, result) if first is not None else result
        )
        if result.output is None:
            continue
        governance_records_by_window[result.request.window_id].extend(result.output.governance_records)
        for raw_block in result.output.idea_blocks:
            block = raw_block.model_copy(update={"extraction_method": "openai_compatible_inference"})
            key = _block_key(block)
            existing = blocks_by_key.get(key)
            if existing is None:
                blocks_by_key[key] = block
                owner_by_key[key] = result.request.window_id
                continue
            blocks_by_key[key] = _merge_blocks(existing, block)
            duplicate_window_ids.add(result.request.window_id)
    return retried_window_ids


def _combine_inference_results(first: InferenceResult, second: InferenceResult) -> InferenceResult:
    return InferenceResult(
        request=second.request,
        output=second.output if second.output is not None else first.output,
        attempt_count=first.attempt_count + second.attempt_count,
        input_tokens=first.input_tokens + second.input_tokens,
        output_tokens=first.output_tokens + second.output_tokens,
        warnings=tuple(dict.fromkeys((*first.warnings, *second.warnings))),
        error=second.error if second.error is not None else first.error,
    )


def _block_key(block: ExtractedIdeaBlock) -> tuple[str, str]:
    return primary_claim_evidence(block).source_element_id, normalize_statement(block.statement)


def _deterministic_governance_owner(
    candidate: ExtractedGovernanceRecordCandidate, windows_by_element: dict[str, list[ExtractionWindow]]
) -> ExtractionWindow | None:
    source_element_id = candidate.evidence[0].source_element_id
    candidates = windows_by_element.get(source_element_id, ())
    return candidates[0] if candidates else None


def _residual_text(window: ExtractionWindow, deterministic_blocks: tuple[ExtractedIdeaBlock, ...]) -> str:
    """Return the residual text after removing exact captured evidence quotes.

    Residual accounting is kept at element/quote level: each
    captured evidence occurrence blanks out its exact quote in the window
    text. Elements that have an exact matching quote consumed by the
    deterministic extractor are dropped from the residual. Elements without a
    captured quote are preserved so the LLM can still extract from them.
    """
    if not deterministic_blocks:
        return window.text

    consumed_quotes: list[str] = []
    for block in deterministic_blocks:
        for occurrence in block.evidence:
            quote = (occurrence.source_quote or "").strip()
            if not quote:
                continue
            consumed_quotes.append(quote)

    residual = window.text
    for quote in consumed_quotes:
        residual = _blank_first_exact(residual, quote)
    return residual.strip() if tokenize_search(residual) else ""


def _blank_first_exact(value: str, consumed: str) -> str:
    index = value.find(consumed)
    if index < 0:
        return value
    return value[:index] + _blank_preserving_lines(consumed) + value[index + len(consumed) :]


def _blank_preserving_lines(value: str) -> str:
    return "".join("\n" if character == "\n" else " " for character in value)


def _deterministic_owner(
    block: ExtractedIdeaBlock, element_id: str, windows_by_element: dict[str, list[ExtractionWindow]]
) -> ExtractionWindow | None:
    windows = windows_by_element.get(element_id, [])
    if not windows:
        return None
    quote = primary_claim_evidence(block).source_quote.strip()
    if quote:
        for window in windows:
            if quote in window.text:
                return window
    statement = block.statement.strip()
    if statement:
        for window in windows:
            if statement in window.text:
                return window
    return windows[0]


def _merge_blocks(existing: ExtractedIdeaBlock, incoming: ExtractedIdeaBlock) -> ExtractedIdeaBlock:
    entities = _unique_models((*existing.entities, *incoming.entities), _entity_key)
    relationships = _unique_models((*existing.relationships, *incoming.relationships), _relationship_key)
    evidence = _unique_models(
        (*existing.evidence, *incoming.evidence),
        lambda item: (item.source_element_id, item.source_quote, item.polarity),
    )
    return incoming.model_copy(
        update={
            "confidence": "extracted",
            "entities": list(entities),
            "relationships": list(relationships),
            "evidence": list(evidence),
            "tags": list(dict.fromkeys((*existing.tags, *incoming.tags))),
            "metadata": {**existing.metadata, **incoming.metadata},
            "extraction_method": "openai_compatible_inference",
        }
    )


def _unique_models(items: tuple[Any, ...], key_function: Callable[[Any], tuple[Any, ...]]) -> tuple[Any, ...]:
    by_key: dict[tuple[Any, ...], Any] = {}
    for item in items:
        by_key.setdefault(key_function(item), item)
    return tuple(by_key.values())


def _entity_key(entity: ExtractedEntity) -> tuple[str, str, str, str | None]:
    return normalize_entity_name(entity.name), entity.kind, entity.role, entity.external_id


def _relationship_key(relationship: ExtractedRelationship) -> tuple[str, str, str, str, str]:
    return (
        normalize_entity_name(relationship.source),
        relationship.relation_type,
        normalize_entity_name(relationship.target),
        relationship.confidence,
        relationship.polarity,
    )
