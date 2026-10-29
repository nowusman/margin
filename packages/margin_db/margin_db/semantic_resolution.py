from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from margin_schema.ids import canonical_json
from margin_schema.text_normalization import normalize_canonical_text, normalize_statement, tokenize_keywords

SEMANTIC_KEY_VERSION = "semantic-key-v1"
SEMANTIC_RESOLUTION_RULE_VERSION = "semantic-resolution-rules-v1"
SEMANTIC_RESOLUTION_TASK_TYPE = "semantic_resolution_review"
SEMANTIC_OUTCOMES = ("equivalent", "refines", "contradicts", "supersedes", "related", "distinct", "uncertain")
SemanticOutcome = Literal["equivalent", "refines", "contradicts", "supersedes", "related", "distinct", "uncertain"]


class SemanticClassifier(Protocol):
    model_version: str

    def classify(
        self, *, left_statement: str, right_statement: str, protected_fields: Mapping[str, object]
    ) -> Mapping[str, object]: ...


class SemanticResolutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: SemanticOutcome
    rationale: str = Field(min_length=1)
    protected_field_differences: dict[str, object] = Field(default_factory=dict)
    rule_version: str = SEMANTIC_RESOLUTION_RULE_VERSION
    model_version: str = Field(min_length=1)
    score: float = Field(ge=0, le=1)


@dataclass(frozen=True)
class ProtectedFieldComparison:
    left: dict[str, object]
    right: dict[str, object]
    differences: dict[str, dict[str, object]]

    @property
    def guard_conflicts(self) -> tuple[str, ...]:
        return tuple(sorted(self.differences))

    def to_dict(self) -> dict[str, object]:
        return {
            "left": dict(self.left),
            "right": dict(self.right),
            "differences": dict(self.differences),
            "guard_conflicts": list(self.guard_conflicts),
        }


def _protected_field_differences(comparison: ProtectedFieldComparison) -> dict[str, object]:
    return {name: values for name, values in comparison.differences.items()}


def canonical_semantic_key(
    *,
    domain_id: str,
    normalized_statement: str,
    type: str,
    polarity: str,
    qualifiers: Mapping[str, object] | None = None,
    valid_from: date | None = None,
    valid_to: date | None = None,
    resolved_entity_ids: Collection[str] = (),
) -> str:
    """Return the stable digest for one domain-scoped semantic proposition.

    The clean-target schema uses ``domain_id`` as its tenant/domain ownership
    scope; no separate tenant relation exists in this release.
    """

    payload = canonical_semantic_key_payload(
        domain_id=domain_id,
        normalized_statement=normalized_statement,
        type=type,
        polarity=polarity,
        qualifiers=qualifiers,
        valid_from=valid_from,
        valid_to=valid_to,
        resolved_entity_ids=resolved_entity_ids,
    )
    return "sk_" + sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def canonical_semantic_key_payload(
    *,
    domain_id: str,
    normalized_statement: str,
    type: str,
    polarity: str,
    qualifiers: Mapping[str, object] | None = None,
    valid_from: date | None = None,
    valid_to: date | None = None,
    resolved_entity_ids: Collection[str] = (),
) -> dict[str, object]:
    return {
        "key_version": SEMANTIC_KEY_VERSION,
        "domain_id": normalize_canonical_text(domain_id),
        "normalized_statement": normalize_statement(normalized_statement),
        "type": normalize_canonical_text(type),
        "polarity": normalize_canonical_text(polarity),
        "qualifiers": _canonical_qualifiers(qualifiers or {}),
        "valid_from": valid_from.isoformat() if valid_from else None,
        "valid_to": valid_to.isoformat() if valid_to else None,
        "resolved_entity_ids": sorted({str(item).strip() for item in resolved_entity_ids if str(item).strip()}),
    }


def protected_fields_for_revision(
    revision: object, *, domain_id: str = "", resolved_entity_ids: Collection[str] | None = None
) -> dict[str, object]:
    metadata = getattr(revision, "metadata_", None) or {}
    statement = str(getattr(revision, "statement", ""))
    if resolved_entity_ids is None:
        resolved_entity_ids = _metadata_ids(metadata)
    return {
        "domain_id": domain_id,
        "type": _normalized_value(getattr(revision, "type", "")),
        "polarity": _normalized_value(getattr(revision, "polarity", "")),
        "numbers": list(_numbers(statement)),
        "units": list(_units(statement)),
        "versions": list(_versions(statement)),
        "negation": list(_negation(statement)),
        "modality": list(_modality(statement)),
        "dates": list(_dates(statement, revision)),
        "qualifiers": _canonical_qualifiers(getattr(revision, "qualifiers", None) or {}),
        "qualifier_tokens": list(_qualifier_tokens(statement)),
        "canonical_entity_ids": sorted({str(item) for item in resolved_entity_ids}),
        "governance_record_id": _governance_record_id(metadata),
        "valid_from": _date_value(getattr(revision, "valid_from", None)),
        "valid_to": _date_value(getattr(revision, "valid_to", None)),
    }


def compare_protected_fields(
    left_revision: object,
    right_revision: object,
    *,
    left_domain_id: str = "",
    right_domain_id: str = "",
    left_entity_ids: Collection[str] | None = None,
    right_entity_ids: Collection[str] | None = None,
) -> ProtectedFieldComparison:
    left = protected_fields_for_revision(left_revision, domain_id=left_domain_id, resolved_entity_ids=left_entity_ids)
    right = protected_fields_for_revision(
        right_revision, domain_id=right_domain_id, resolved_entity_ids=right_entity_ids
    )
    differences = {
        name: {"left": left[name], "right": right[name]} for name in sorted(left) if left[name] != right[name]
    }
    return ProtectedFieldComparison(left=left, right=right, differences=differences)


def classify_semantic_pair(
    left_revision: object,
    right_revision: object,
    *,
    comparison: ProtectedFieldComparison | None = None,
    score: float = 0.0,
    classifier: SemanticClassifier | None = None,
) -> SemanticResolutionResult:
    """Classify a pair while making deterministic protected-field guards authoritative."""

    comparison = comparison or compare_protected_fields(left_revision, right_revision)
    bounded_score = min(1.0, max(0.0, float(score)))
    if comparison.guard_conflicts:
        outcome: SemanticOutcome = "contradicts" if "polarity" in comparison.guard_conflicts else "uncertain"
        return SemanticResolutionResult(
            outcome=outcome,
            rationale="deterministic protected-field guard blocked equivalence",
            protected_field_differences=_protected_field_differences(comparison),
            model_version=SEMANTIC_RESOLUTION_RULE_VERSION,
            score=bounded_score,
        )

    left_statement = normalize_statement(str(getattr(left_revision, "normalized_statement", "")))
    right_statement = normalize_statement(str(getattr(right_revision, "normalized_statement", "")))
    if left_statement == right_statement:
        return SemanticResolutionResult(
            outcome="equivalent",
            rationale="normalized statements and all protected fields match",
            protected_field_differences={},
            model_version=SEMANTIC_RESOLUTION_RULE_VERSION,
            score=bounded_score,
        )

    if classifier is None:
        return SemanticResolutionResult(
            outcome="uncertain",
            rationale="no configured semantic classifier; human review is required",
            protected_field_differences={},
            model_version=SEMANTIC_RESOLUTION_RULE_VERSION,
            score=bounded_score,
        )

    try:
        raw = classifier.classify(
            left_statement=str(getattr(left_revision, "statement", "")),
            right_statement=str(getattr(right_revision, "statement", "")),
            protected_fields=comparison.to_dict(),
        )
        return SemanticResolutionResult.model_validate(
            {
                **dict(raw),
                "protected_field_differences": comparison.differences,
                "model_version": str(raw.get("model_version") or classifier.model_version),
                "score": raw.get("score", bounded_score),
            }
        )
    except Exception as exc:
        return SemanticResolutionResult(
            outcome="uncertain",
            rationale=f"semantic classifier output was unavailable or invalid: {exc}",
            protected_field_differences={},
            model_version=getattr(classifier, "model_version", SEMANTIC_RESOLUTION_RULE_VERSION),
            score=bounded_score,
        )


class SemanticClassifierUnavailable(RuntimeError):
    """Raised when the configured semantic classifier cannot be used."""


class ConfiguredSemanticClassifier:
    """Use the active catalog model for review suggestions only."""

    def __init__(self, session: object, *, timeout_seconds: int = 30) -> None:
        from margin_db.model_registry import ModelRegistryError, configured_active_model

        try:
            config = configured_active_model(session, "semantic_resolution")  # type: ignore[arg-type]
        except ModelRegistryError as exc:
            raise SemanticClassifierUnavailable(str(exc)) from exc
        if config is None:
            raise SemanticClassifierUnavailable("no active semantic_resolution model configured")
        if config.last_test_status != "healthy":
            raise SemanticClassifierUnavailable("semantic_resolution model has no successful health test")
        if not (config.capabilities.get("structured_output") or config.capabilities.get("json_mode")):
            raise SemanticClassifierUnavailable("semantic_resolution model must support structured JSON output")
        from margin_inference import InferenceClient

        self._client = InferenceClient(
            base_url=config.endpoint_url,
            model=config.model_name,
            secret_reference=config.secret_reference,
            timeout_seconds=timeout_seconds,
        )
        self.model_version = config.id
        self._temperature = config.temperature_default
        self._max_tokens = config.max_tokens
        self._timeout_seconds = timeout_seconds

    def classify(
        self, *, left_statement: str, right_statement: str, protected_fields: Mapping[str, object]
    ) -> Mapping[str, object]:
        prompt = json.dumps(
            {
                "left_statement": left_statement,
                "right_statement": right_statement,
                "protected_fields": protected_fields,
                "allowed_outcomes": list(SEMANTIC_OUTCOMES),
                "output_schema": {
                    "outcome": "one allowed outcome",
                    "rationale": "short explanation",
                    "score": "number between 0 and 1",
                },
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        response = self._client.chat_completions(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classify the semantic relationship. Return one JSON object only. "
                        "Do not merge, rewrite, or select a survivor."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            timeout_seconds=self._timeout_seconds,
            event="semantic_resolution",
        )
        content = response.content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE)
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError("semantic classifier must return a JSON object")
        return parsed


def configured_semantic_classifier(session: object) -> ConfiguredSemanticClassifier | None:
    try:
        return ConfiguredSemanticClassifier(session)
    except SemanticClassifierUnavailable:
        return None


def _canonical_qualifiers(value: Mapping[str, object]) -> object:
    def normalize(item: object) -> object:
        if isinstance(item, Mapping):
            return {
                normalize_canonical_text(str(key)): normalize(child)
                for key, child in sorted(item.items(), key=lambda pair: str(pair[0]).casefold())
            }
        if isinstance(item, list | tuple | set | frozenset):
            values = [normalize(child) for child in item]
            return sorted(values, key=canonical_json)
        if isinstance(item, str):
            return normalize_canonical_text(item)
        return item

    return normalize(value)


def _normalized_value(value: object) -> str:
    return normalize_canonical_text(str(value))


def _metadata_ids(metadata: Mapping[str, object]) -> tuple[str, ...]:
    for key in ("resolved_entity_ids", "canonical_entity_ids", "entity_ids"):
        value = metadata.get(key)
        if isinstance(value, Sequence) and not isinstance(value, str | bytes):
            return tuple(str(item) for item in value if str(item).strip())
    return ()


def _governance_record_id(metadata: Mapping[str, object]) -> str | None:
    for key in ("governance_record_id", "governance_id", "record_id"):
        value = metadata.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _date_value(value: object) -> str | None:
    return value.isoformat() if isinstance(value, date) else (str(value) if value else None)


_VERSION_PATTERN = re.compile(r"\bv\d+(?:\.\d+)*\b", re.IGNORECASE)
_DATE_PATTERN = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_NUMBER_PATTERN = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?(?![\w.])")
_UNIT_PATTERN = re.compile(
    r"(?<!\w)(?:%|ms|milliseconds?|s|sec(?:ond)?s?|m|min(?:ute)?s?|h|hours?|d|days?|"
    r"kb|mb|gb|tb|users?|requests?|retries?|items?|records?)(?!\w)",
    re.IGNORECASE,
)
_NEGATION_TOKENS = frozenset({"no", "not", "never", "without", "neither", "cannot", "can't", "deny"})
_MODALITY_TOKENS = frozenset({"must", "shall", "should", "may", "might", "can", "could", "required", "prohibited"})
_QUALIFIER_TOKENS = frozenset({"except", "unless", "if", "when", "only", "provided"})


def _versions(text: str) -> tuple[str, ...]:
    return tuple(sorted(match.casefold() for match in _VERSION_PATTERN.findall(text)))


def _dates(text: str, revision: object) -> tuple[str, ...]:
    values = set(_DATE_PATTERN.findall(text))
    for name in ("valid_from", "valid_to"):
        value = getattr(revision, name, None)
        if value:
            values.add(value.isoformat() if isinstance(value, date) else str(value))
    return tuple(sorted(values))


def _numbers(text: str) -> tuple[str, ...]:
    version_spans = [match.span() for match in _VERSION_PATTERN.finditer(text)]
    return tuple(
        sorted(
            match.group(0).replace(",", ".")
            for match in _NUMBER_PATTERN.finditer(text)
            if not any(start <= match.start() < end for start, end in version_spans)
        )
    )


def _units(text: str) -> tuple[str, ...]:
    return tuple(sorted(match.casefold() for match in _UNIT_PATTERN.findall(text)))


def _negation(text: str) -> tuple[str, ...]:
    values = set(tokenize_keywords(text)) & _NEGATION_TOKENS
    if re.search(r"\bcan['’]t\b", text, re.IGNORECASE):
        values.add("can't")
    return tuple(sorted(values))


def _modality(text: str) -> tuple[str, ...]:
    return tuple(sorted(set(tokenize_keywords(text)) & _MODALITY_TOKENS))


def _qualifier_tokens(text: str) -> tuple[str, ...]:
    return tuple(sorted(set(tokenize_keywords(text)) & _QUALIFIER_TOKENS))


__all__ = [
    "ConfiguredSemanticClassifier",
    "ProtectedFieldComparison",
    "SEMANTIC_KEY_VERSION",
    "SEMANTIC_OUTCOMES",
    "SEMANTIC_RESOLUTION_RULE_VERSION",
    "SEMANTIC_RESOLUTION_TASK_TYPE",
    "SemanticResolutionResult",
    "canonical_semantic_key",
    "canonical_semantic_key_payload",
    "classify_semantic_pair",
    "compare_protected_fields",
    "configured_semantic_classifier",
    "protected_fields_for_revision",
]
