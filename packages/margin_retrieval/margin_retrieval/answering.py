"""Catalog-backed, citation-grounded answer synthesis."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from sqlalchemy.orm import Session

from margin_db.model_catalog_models import ModelConfiguration
from margin_db.model_registry import ModelRegistryError, configured_active_model
from margin_inference import InferenceClient, InferenceTimeoutError, InferenceUnavailableError
from margin_retrieval.interfaces import Citation, EvidenceItem, RetrievalScope
from margin_retrieval.policy import CitationSufficiencyError, _has_cited_evidence_chain
from margin_schema.text_normalization import tokenize_keywords

ANSWER_SYNTHESIS_TIMEOUT_SECONDS = 30
_MAX_ANSWER_CHARACTERS = 12_000
_CITATION_ID_PATTERN = re.compile(r"\[([^\]]+)\]")


@dataclass(frozen=True)
class AnswerSynthesisResult:
    answer_markdown: str | None
    citation_ids: tuple[str, ...]
    unavailable_reason: str | None
    model: dict[str, object] | None
    latency_ms: int
    validation: str


class CatalogAnswerSynthesizer:
    """Generate an answer only from evidence the caller may read."""

    def synthesize(
        self,
        session: Session,
        *,
        query: str,
        items: tuple[EvidenceItem, ...],
        scope: RetrievalScope,
        briefing: str = "",
        output_language: str = "",
        conversation_history: tuple[str, ...] = (),
    ) -> AnswerSynthesisResult:
        citations = _readable_citations(items)
        if not citations:
            return _unavailable("no_readable_citations")

        try:
            configuration = configured_active_model(session, "answer_synthesis")
        except ModelRegistryError:
            return _unavailable("answer_model_misconfigured")
        if configuration is None:
            return _unavailable("answer_model_unavailable")
        if not _model_is_healthy(session, configuration.id):
            return _unavailable("answer_model_unhealthy")
        if not _model_allows_scope(configuration.capabilities, scope):
            return _unavailable("answer_model_not_authorized")

        started = perf_counter()
        model_metadata = {
            "id": configuration.id,
            "assignment_id": configuration.id,
            "name": configuration.model_name,
            "version": configuration.model_name,
        }
        try:
            response = InferenceClient(
                base_url=configuration.endpoint_url,
                model=configuration.model_name,
                secret_reference=configuration.secret_reference,
                timeout_seconds=ANSWER_SYNTHESIS_TIMEOUT_SECONDS,
            ).chat_completions(
                messages=_messages(
                    system_prompt=configuration.system_prompt,
                    query=query,
                    items=items,
                    citations=citations,
                    briefing=briefing,
                    output_language=output_language,
                    conversation_history=conversation_history,
                ),
                temperature=configuration.temperature_default,
                max_tokens=configuration.max_tokens,
                timeout_seconds=ANSWER_SYNTHESIS_TIMEOUT_SECONDS,
                event="answer_synthesis",
                redact_content=True,
                response_format=_answer_response_format(configuration.capabilities),
            )
        except InferenceTimeoutError:
            return _unavailable("answer_model_timeout", model=model_metadata, latency_ms=_elapsed_ms(started))
        except (InferenceUnavailableError, ValueError):
            return _unavailable("answer_model_unavailable", model=model_metadata, latency_ms=_elapsed_ms(started))

        try:
            answer, citation_ids = _parse_answer(response.content)
            CitationSufficiencyChecker().validate(answer, items)
            if set(citation_ids) != set(_CITATION_ID_PATTERN.findall(answer)):
                raise CitationSufficiencyError("answer citation list does not match answer citations")
        except (json.JSONDecodeError, CitationSufficiencyError, TypeError, ValueError):
            return _unavailable(
                "answer_validation_failed",
                model={**model_metadata, "usage": _usage_dict(response.usage)},
                latency_ms=response.latency_ms,
            )

        return AnswerSynthesisResult(
            answer_markdown=answer,
            citation_ids=citation_ids,
            unavailable_reason=None,
            model={**model_metadata, "usage": _usage_dict(response.usage)},
            latency_ms=response.latency_ms,
            validation="valid",
        )


class CitationSufficiencyChecker:
    """Reject uncited, unknown, or unsupported material answer statements."""

    def validate(self, answer_markdown: str, items: tuple[EvidenceItem, ...]) -> None:
        items_by_citation = {
            citation.citation_id: item
            for item in items
            for citation in _readable_citations((item,)).values()
            if _has_cited_evidence_chain(citation)
        }
        if not items_by_citation:
            raise CitationSufficiencyError("answer has no complete evidence chain")

        for line in answer_markdown.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            line_citations = set(_CITATION_ID_PATTERN.findall(stripped))
            for sentence in _material_sentences(stripped):
                citations = set(_CITATION_ID_PATTERN.findall(sentence)) or line_citations
                if not citations:
                    raise CitationSufficiencyError(f"material answer sentence lacks citation: {sentence}")
                unknown = citations - set(items_by_citation)
                if unknown:
                    raise CitationSufficiencyError(
                        f"answer cites evidence outside retrieval context: {sorted(unknown)[0]}"
                    )
                if not any(
                    _sentence_supported_by_item(sentence, items_by_citation[citation]) for citation in citations
                ):
                    raise CitationSufficiencyError(f"material answer sentence is not supported: {sentence}")


def _model_is_healthy(session: Session, model_id: str) -> bool:
    model = session.get(ModelConfiguration, model_id)
    return model is not None and model.last_test_status == "healthy"


def _model_allows_scope(capabilities: dict[str, object], scope: RetrievalScope) -> bool:
    if capabilities.get("text_input") is False:
        return False
    if not (capabilities.get("structured_output") or capabilities.get("json_mode")):
        return False
    allowed_domains = _string_set(capabilities.get("allowed_domains"))
    if scope.all_domain_ids_authorized and allowed_domains and "*" not in allowed_domains:
        return False
    if allowed_domains and not _scope_allowed(scope.domain_ids, allowed_domains):
        return False
    allowed_sensitivities = _string_set(capabilities.get("allowed_sensitivities"))
    return not allowed_sensitivities or _scope_allowed(scope.sensitivities, allowed_sensitivities)


def _answer_response_format(capabilities: dict[str, object]) -> dict[str, object]:
    """Request the strongest structured response contract the model advertises."""

    if capabilities.get("structured_output"):
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "margin_cited_answer",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "answer": {"type": "string", "minLength": 1, "maxLength": _MAX_ANSWER_CHARACTERS},
                        "citation_ids": {
                            "type": "array",
                            "items": {"type": "string", "minLength": 1},
                            "minItems": 1,
                            "uniqueItems": True,
                        },
                    },
                    "required": ["answer", "citation_ids"],
                },
            },
        }
    return {"type": "json_object"}


def _scope_allowed(values: tuple[str, ...], allowed: set[str]) -> bool:
    return "*" in allowed or not values or set(values).issubset(allowed)


def _messages(
    *,
    system_prompt: str,
    query: str,
    items: tuple[EvidenceItem, ...],
    citations: dict[str, Citation],
    briefing: str = "",
    output_language: str = "",
    conversation_history: tuple[str, ...] = (),
) -> list[dict[str, str]]:
    evidence = [
        {
            "idea_block_id": item.metadata.get("idea_block_id"),
            "revision_id": item.metadata.get("idea_block_revision_id"),
            "statement": item.text,
            "entities": item.metadata.get("entities", []),
            "governance": item.metadata.get("governance", []),
            "relations": item.metadata.get("relations", []),
            "citations": [citation.citation_id for citation in item.citations if citation.citation_id in citations],
        }
        for item in items
    ]
    citation_context = {
        citation_id: {
            "quote": citation.quote,
            "polarity": citation.polarity,
            "valid_from": citation.valid_from.isoformat() if citation.valid_from else None,
            "valid_to": citation.valid_to.isoformat() if citation.valid_to else None,
            "source_document_id": citation.source_document_id,
            "source_version_id": citation.source_version_id,
            "source_element_id": citation.source_element_id,
            "governance_record_id": citation.governance_record_id,
            "governance_record_revision_id": citation.governance_record_revision_id,
            "governance_record_type": citation.governance_record_type,
        }
        for citation_id, citation in citations.items()
    }
    instructions = (
        "Return exactly one JSON object with keys answer and citation_ids. answer is Markdown. "
        "Every material statement must have one or more inline [citation_id] references. "
        "Use only supplied citation IDs. Do not invent facts, resolve contradictions, or present superseded "
        "or time-limited evidence as current without stating that condition. citation_ids must contain exactly "
        "the distinct citation IDs used in answer."
    )
    if briefing:
        instructions += (
            " The domain_context field is background about the domain, not evidence. "
            "Do not state a fact that appears only in domain_context, and do not cite it."
        )
    if output_language:
        instructions += f" Write the answer in {output_language}."
    if conversation_history:
        instructions += (
            " The conversation_history field is earlier turns in this conversation. "
            "It is context, not evidence. Do not restate a claim from it without a citation."
        )
    system_content = system_prompt.strip()
    if system_content:
        system_content = f"{system_content}\n\n{instructions}"
    else:
        system_content = instructions
    payload: dict[str, object] = {
        "instructions": instructions,
        "query": query,
        "evidence": evidence,
        "citations": citation_context,
    }
    if briefing:
        payload["domain_context"] = briefing
    if conversation_history:
        payload["conversation_history"] = list(conversation_history)
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _parse_answer(content: str) -> tuple[str, tuple[str, ...]]:
    payload = json.loads(content)
    if not isinstance(payload, dict) or set(payload) != {"answer", "citation_ids"}:
        raise ValueError("answer response must contain only answer and citation_ids")
    answer = payload["answer"]
    citation_ids = payload["citation_ids"]
    if not isinstance(answer, str) or not answer.strip() or len(answer) > _MAX_ANSWER_CHARACTERS:
        raise ValueError("answer is missing or exceeds the response limit")
    if (
        not isinstance(citation_ids, list)
        or not citation_ids
        or not all(isinstance(value, str) and value for value in citation_ids)
    ):
        raise ValueError("answer citation_ids must be a non-empty string list")
    if len(set(citation_ids)) != len(citation_ids):
        raise ValueError("answer citation_ids must be unique")
    return answer.strip(), tuple(citation_ids)


def _readable_citations(items: tuple[EvidenceItem, ...]) -> dict[str, Citation]:
    return {
        citation.citation_id: citation
        for item in items
        for citation in item.citations
        if not citation.redacted and _has_cited_evidence_chain(citation)
    }


def _material_sentences(line: str) -> list[str]:
    stripped = line.removeprefix("- ").strip()
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+(?!\[)", stripped) if part.strip()]


def _sentence_supported_by_item(sentence: str, item: EvidenceItem) -> bool:
    without_citations = _CITATION_ID_PATTERN.sub("", sentence)
    subject = without_citations.rsplit(":", 1)[-1].strip()
    statement_tokens = _support_tokens(subject or without_citations)
    evidence_tokens = _support_tokens(
        " ".join(
            value
            for value in [item.text, *(citation.quote or "" for citation in item.citations if not citation.redacted)]
            if value
        )
    )
    overlap = statement_tokens & evidence_tokens
    if not statement_tokens or not evidence_tokens or not overlap:
        return False
    if len(statement_tokens) == 1:
        return True
    return len(overlap) >= 2 and len(overlap) / len(statement_tokens) >= 0.35


def _support_tokens(value: str) -> set[str]:
    return set(
        tokenize_keywords(
            value,
            stopwords=frozenset(
                {"a", "an", "and", "are", "as", "by", "for", "from", "in", "is", "of", "or", "the", "to", "with"}
            ),
        )
    )


def _unavailable(reason: str, *, model: dict[str, object] | None = None, latency_ms: int = 0) -> AnswerSynthesisResult:
    return AnswerSynthesisResult(None, (), reason, model, latency_ms, "unavailable")


def _elapsed_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


def _usage_dict(usage: Any) -> dict[str, int]:
    return {
        "prompt_tokens": int(usage.prompt_tokens),
        "completion_tokens": int(usage.completion_tokens),
        "total_tokens": int(usage.total_tokens),
    }


def _string_set(value: object) -> set[str]:
    if isinstance(value, list | tuple):
        return {item for item in value if isinstance(item, str)}
    return set()
