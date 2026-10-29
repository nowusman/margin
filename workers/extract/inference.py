from __future__ import annotations

import json
import logging
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from margin_inference import (
    InferenceError,
    InferencePolicyError,
    InferenceTimeoutError,
    InferenceUnavailableError,
    chat_completions_request,
    chat_completions_url,
    is_http_endpoint,
    probe_openai_compatible_endpoint,
)

__all__ = [
    "ExtractionRequest",
    "InferenceError",
    "InferenceInvalidOutputError",
    "InferencePolicyError",
    "InferenceResult",
    "InferenceTimeoutError",
    "InferenceUnavailableError",
    "OpenAICompatibleExtractor",
    "chat_completions_request",
    "chat_completions_url",
    "probe_openai_compatible_endpoint",
]
from margin_schema.extraction import ExtractorOutput
from margin_schema.text_normalization import normalize_statement
from workers.extract.prompts import EXTRACTOR_SYSTEM_PROMPT, validate_prompt_output

logger = logging.getLogger(__name__)


class InferenceInvalidOutputError(InferenceUnavailableError):
    """Raised when inference responds but does not match the extraction schema."""


@dataclass(frozen=True)
class ExtractionRequest:
    window_id: str
    source_element_id: str
    text: str
    content_type: str
    source_page_id: str | None = None
    page_number: int | None = None
    section_heading: str | None = None
    section_path: str = ""
    preceding_context: tuple[dict[str, object], ...] = ()
    following_context: tuple[dict[str, object], ...] = ()
    document_metadata: dict[str, object] = field(default_factory=dict)
    window_strategy: str = "text_overlap"
    primary_source_element_ids: tuple[str, ...] = ()
    eligible_citable_elements: tuple[dict[str, object], ...] = ()
    governed_candidates: tuple[dict[str, object], ...] = ()
    ontology_version: str | None = None
    domain_charter_version: int | None = None
    domain_charter_context: str = ""
    playbook_context: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True)
class InferenceResult:
    request: ExtractionRequest
    output: ExtractorOutput | None
    attempt_count: int
    input_tokens: int
    output_tokens: int
    warnings: tuple[str, ...] = ()
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.output is not None and self.error is None


@dataclass(frozen=True)
class OpenAICompatibleExtractor:
    base_url: str
    model: str
    secret_reference: str | None = None
    temperature: float = 0.0
    max_tokens: int = 0
    system_prompt: str = EXTRACTOR_SYSTEM_PROMPT
    timeout_seconds: int = 120
    max_attempts: int = 2
    max_concurrency: int = 2
    retry_backoff_seconds: float = 0.25

    def __post_init__(self) -> None:
        if not is_http_endpoint(self.base_url):
            raise InferencePolicyError("inference extractor requires an HTTP OpenAI-compatible endpoint")

    def extract(self, *, source_element_id: str, text: str, source_page_id: str | None = None) -> ExtractorOutput:
        result = self.extract_many(
            (
                ExtractionRequest(
                    window_id=f"window:{source_element_id}",
                    source_element_id=source_element_id,
                    source_page_id=source_page_id,
                    text=text,
                    content_type="text",
                    page_number=None,
                    section_heading=None,
                    section_path="",
                    preceding_context=(),
                    following_context=(),
                    document_metadata={},
                ),
            )
        )[0]
        if result.output is None:
            raise InferenceUnavailableError(result.error or "inference request failed")
        return result.output

    def extract_many(self, requests: tuple[ExtractionRequest, ...]) -> tuple[InferenceResult, ...]:
        if not requests:
            return ()
        results: list[InferenceResult | None] = [None] * len(requests)
        with ThreadPoolExecutor(max_workers=max(1, self.max_concurrency)) as executor:
            futures = {executor.submit(self._extract_request, request): index for index, request in enumerate(requests)}
            for future in as_completed(futures):
                results[futures[future]] = future.result()
        if any(result is None for result in results):
            raise RuntimeError("inference executor returned an incomplete result set")
        return tuple(result for result in results if result is not None)

    def _extract_request(self, request: ExtractionRequest) -> InferenceResult:
        last_error: str | None = None
        for attempt in range(1, max(1, self.max_attempts) + 1):
            try:
                output, input_tokens, output_tokens = self._request_once(request)
                return InferenceResult(
                    request=request,
                    output=output,
                    attempt_count=attempt,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            except (InferenceInvalidOutputError, InferenceUnavailableError) as exc:
                last_error = str(exc)
                if attempt < max(1, self.max_attempts):
                    time.sleep(self._retry_delay(attempt - 1))
        return InferenceResult(
            request=request,
            output=None,
            attempt_count=max(1, self.max_attempts),
            input_tokens=_estimate_tokens(json.dumps(_request_payload(request), ensure_ascii=False)),
            output_tokens=0,
            warnings=("inference_failed",),
            error=last_error or "inference request failed",
        )

    def _request_once(self, extraction_request: ExtractionRequest) -> tuple[ExtractorOutput, int, int]:
        messages: list[dict[str, object]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": json.dumps(_request_payload(extraction_request), ensure_ascii=False)},
        ]
        body = chat_completions_request(
            base_url=self.base_url,
            model=self.model,
            secret_reference=self.secret_reference,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout_seconds=self.timeout_seconds,
            event="extract",
            redact_content=True,
        )
        payload = {"model": self.model, "temperature": self.temperature, "messages": messages}
        try:
            choices = body["choices"]
            if not isinstance(choices, list):
                raise TypeError("choices is not a list")
            first_choice = choices[0]
            if not isinstance(first_choice, dict):
                raise TypeError("choice is not an object")
            message = first_choice["message"]
            if not isinstance(message, dict):
                raise TypeError("message is not an object")
            content = message["content"]
            if not isinstance(content, str):
                raise TypeError("message content is not text")
        except (KeyError, IndexError, TypeError) as exc:
            raise InferenceUnavailableError("inference response missing chat content") from exc
        try:
            output = validate_prompt_output(
                content,
                supplied_candidate_ids={
                    str(candidate["canonical_id"])
                    for candidate in extraction_request.governed_candidates
                    if isinstance(candidate, dict) and candidate.get("canonical_id")
                },
            )
            self._validate_citations(output, extraction_request)
            input_tokens, output_tokens = _usage_tokens(body, payload, content)
            return output, input_tokens, output_tokens
        except ValueError as exc:
            raise InferenceInvalidOutputError("inference returned invalid extraction output") from exc

    @staticmethod
    def _validate_citations(output: ExtractorOutput, request: ExtractionRequest) -> None:
        eligible = _EligibleElementIndex.from_request(request)
        for idea_block in output.idea_blocks:
            for evidence in idea_block.evidence:
                if not eligible.accepts(evidence):
                    raise ValueError("inference cited a source_element_id outside the eligible-element set")
                if not _quote_present(evidence.source_quote, eligible.text_for(evidence)):
                    raise ValueError("inference source_quote is not present in cited element text")
        for candidate in output.governance_records:
            for evidence in candidate.evidence:
                if not eligible.accepts(evidence):
                    raise ValueError("inference cited a source_element_id outside the eligible-element set")
                if not _quote_present(evidence.source_quote, eligible.text_for(evidence)):
                    raise ValueError("inference source_quote is not present in cited element text")

    def _retry_delay(self, attempt: int) -> float:
        base_delay = max(self.retry_backoff_seconds, 0) * (2**attempt)
        return base_delay + random.uniform(0, base_delay * 0.2 if base_delay else 0)


def _quote_present(quote: str, source: str) -> bool:
    normalized_source = _normalized_quote(source)
    normalized_quote = _normalized_quote(quote)
    if not normalized_quote:
        return True
    if normalized_quote in normalized_source:
        return True
    parts = [part for part in re.split(r"\s+\|\s+|:\s*", quote) if part.strip()]
    return bool(parts) and all(_normalized_quote(part) in normalized_source for part in parts)


def _normalized_quote(value: str) -> str:
    return normalize_statement(value)


class _EligibleElementIndex:
    """Resolve citations against the window's eligible citable elements.

    Citations are valid only when the cited element id is in the eligible set.
    Context-only elements (preceding / following) and any element outside the
    window are rejected.
    """

    def __init__(self, entries: list[dict[str, object]]) -> None:
        self._entries = entries
        self._by_id: dict[str, dict[str, object]] = {
            str(entry["source_element_id"]): entry for entry in entries if "source_element_id" in entry
        }

    @classmethod
    def from_request(cls, request: ExtractionRequest) -> _EligibleElementIndex:
        entries: list[dict[str, object]] = [
            {
                "source_element_id": request.source_element_id,
                "source_page_id": request.source_page_id,
                "kind": request.content_type,
                "page_number": request.page_number,
                "text": request.text,
                "role": "primary",
                "text_role": "body",
            }
        ]
        for entry in request.eligible_citable_elements:
            if not isinstance(entry, dict):
                continue
            entries.append(dict(entry))
        return cls(entries)

    def accepts(self, evidence: object) -> bool:
        element_id = getattr(evidence, "source_element_id", None)
        if not element_id:
            return False
        return str(element_id) in self._by_id

    def text_for(self, evidence: object) -> str:
        entry = self._by_id.get(str(getattr(evidence, "source_element_id", "")))
        if entry is None:
            return ""
        return str(entry.get("text") or "")


def _request_payload(request: ExtractionRequest) -> dict[str, object]:
    payload: dict[str, object] = {
        "document": request.document_metadata,
        "section": {
            "source_page_id": request.source_page_id,
            "heading": request.section_heading,
            "path": request.section_path,
        },
        "window": {
            "window_id": request.window_id,
            "source_element_id": request.source_element_id,
            "content_type": request.content_type,
            "strategy": request.window_strategy,
            "page_number": request.page_number,
            "text": request.text,
            "preceding_context": list(request.preceding_context),
            "following_context": list(request.following_context),
            "primary_source_element_ids": list(request.primary_source_element_ids),
            "eligible_citable_elements": [
                {
                    "source_element_id": str(entry.get("source_element_id", "")),
                    "source_page_id": entry.get("source_page_id"),
                    "kind": str(entry.get("kind", "")),
                    "page_number": entry.get("page_number"),
                    "text": str(entry.get("text", "")),
                    "role": str(entry.get("role", "eligible")),
                    "text_role": str(entry.get("text_role", "body")),
                }
                for entry in request.eligible_citable_elements
                if isinstance(entry, dict)
            ],
            "governed_candidates": list(request.governed_candidates),
            "ontology_version": request.ontology_version,
        },
    }
    if request.domain_charter_context:
        payload["domain_charter"] = {
            "version": request.domain_charter_version,
            "context": request.domain_charter_context,
        }
    if request.playbook_context:
        payload["domain_playbooks"] = request.playbook_context
    return payload


def _usage_tokens(body: dict[str, object], payload: dict[str, object], content: object) -> tuple[int, int]:
    usage = body.get("usage")
    if isinstance(usage, dict):
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
            return max(0, prompt_tokens), max(0, completion_tokens)
    return _estimate_tokens(json.dumps(payload, ensure_ascii=False)), _estimate_tokens(str(content))


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4) if text else 0
