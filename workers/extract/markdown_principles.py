from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from margin_schema.extraction import ExtractedEntity, ExtractedIdeaBlock, single_claim_evidence
from margin_schema.text_normalization import normalize_entity_name
from workers.extract.markdown_types import ElementLike

PRINCIPLE_FIELD_LABELS = {
    "decision",
    "statement",
    "rationale",
    "rational",
    "implication",
    "implications",
    "consequence",
    "consequences",
    "guidance",
    "examples",
}
PRINCIPLE_STATEMENT_LABELS = {"decision", "statement", "principle", "rule"}
PRINCIPLE_SUPPORT_LABELS = PRINCIPLE_FIELD_LABELS - PRINCIPLE_STATEMENT_LABELS
INLINE_FIELD_PATTERN = re.compile(r"^(?P<label>[A-Za-z][A-Za-z ]{2,30})\s*:\s*(?P<value>.+)$", re.DOTALL)


PrincipleRecord = tuple[ElementLike, str | None, str]


def extract_principle_records(
    elements: Sequence[ElementLike],
    *,
    entity_extractor: Callable[[str], list[ExtractedEntity]],
    tag_extractor: Callable[[str], list[str]],
) -> tuple[list[ExtractedIdeaBlock], set[str]]:
    grouped: dict[str, list[PrincipleRecord]] = {}
    for element in elements:
        if element.kind not in {"paragraph", "bullet"}:
            continue
        text = (element.text_content or "").strip()
        if not text:
            continue
        key = _principle_group_key(element, text)
        if key is None:
            continue
        for label, value in _principle_fields(text, element):
            if label is not None:
                grouped.setdefault(key, []).append((element, label, value))

    blocks: list[ExtractedIdeaBlock] = []
    consumed: set[str] = set()
    for key, records in grouped.items():
        if _has_principle_support(records):
            consumed.update(element.id for element, _, _ in records)
        statement_record = _principle_statement_record(records)
        if statement_record is not None and _has_principle_support(records):
            blocks.append(
                _principle_block(
                    key, statement_record, records, entity_extractor=entity_extractor, tag_extractor=tag_extractor
                )
            )
    return blocks, consumed


def _principle_group_key(element: ElementLike | None, text: str) -> str | None:
    path_parts = _element_path_parts(element)
    lowered_path = " / ".join(path_parts).casefold()
    label, _value = _inline_field(text)
    section_label = _normalized_principle_label(path_parts[-1]) if path_parts else None
    inline_label = _normalized_principle_label(label) if label else None
    if "principle" not in lowered_path and section_label not in PRINCIPLE_FIELD_LABELS:
        return None
    if section_label in PRINCIPLE_FIELD_LABELS and len(path_parts) > 1:
        return " / ".join(path_parts[:-1])
    if inline_label in PRINCIPLE_FIELD_LABELS:
        return " / ".join(path_parts) if path_parts else "Principle"
    return None


def _principle_field(text: str, element: ElementLike | None) -> tuple[str | None, str]:
    label, value = _inline_field(text)
    normalized = _normalized_principle_label(label)
    if normalized in PRINCIPLE_FIELD_LABELS or normalized in PRINCIPLE_STATEMENT_LABELS:
        return normalized, value
    path_parts = _element_path_parts(element)
    section_label = _normalized_principle_label(path_parts[-1]) if path_parts else None
    if section_label in PRINCIPLE_FIELD_LABELS:
        return section_label, text
    if section_label and "principle" in section_label:
        return "statement", text
    return None, text


def _principle_fields(text: str, element: ElementLike | None) -> list[tuple[str | None, str]]:
    fields: list[tuple[str | None, str]] = []
    for line in (line.strip() for line in text.splitlines() if line.strip()):
        label, value = _principle_field(line, element)
        if label is not None:
            fields.append((label, value))
    return fields or [_principle_field(text, element)]


def _inline_field(text: str) -> tuple[str | None, str]:
    match = INLINE_FIELD_PATTERN.match(text.strip())
    if match is None:
        return None, text.strip()
    return match.group("label").strip(), match.group("value").strip()


def _normalized_principle_label(value: str | None) -> str | None:
    if not value:
        return None
    normalized = normalize_entity_name(value)
    normalized = {"rational": "rationale", "consequence": "consequences", "implication": "implications"}.get(
        normalized, normalized
    )
    if normalized in PRINCIPLE_FIELD_LABELS or normalized in PRINCIPLE_STATEMENT_LABELS:
        return normalized
    return normalized if "principle" in normalized else None


def _element_path_parts(element: ElementLike | None) -> list[str]:
    if element is None:
        return []
    raw_path = element.structural_path or element.heading or ""
    parts = [
        part.strip() for part in re.split(r"\s*(?:/|>)\s*", raw_path) if part.strip() and not part.strip().isdigit()
    ]
    return parts or ([element.heading.strip()] if element.heading else [])


def _principle_statement_record(records: list[PrincipleRecord]) -> PrincipleRecord | None:
    return next((record for record in records if record[1] in PRINCIPLE_STATEMENT_LABELS), None)


def _has_principle_support(records: list[PrincipleRecord]) -> bool:
    return bool({label for _, label, _ in records if label} & PRINCIPLE_SUPPORT_LABELS)


def _principle_block(
    key: str,
    statement_record: PrincipleRecord,
    records: list[PrincipleRecord],
    *,
    entity_extractor: Callable[[str], list[ExtractedEntity]],
    tag_extractor: Callable[[str], list[str]],
) -> ExtractedIdeaBlock:
    statement_element, _label, statement_value = statement_record
    title = key.split("/")[-1].strip() or "Architecture principle"
    statement = _principle_statement(title, statement_value)
    aggregate_text = " ".join(value for _, _, value in records)
    is_decision = statement_record[1] == "decision"
    return ExtractedIdeaBlock(
        statement=statement,
        type="decision" if is_decision else "architecture_principle",
        confidence="extracted",
        evidence=single_claim_evidence(
            source_element_id=statement_element.id,
            source_quote=_principle_source_quote(statement_element, statement_value),
        ),
        extraction_method="deterministic_principle",
        entities=entity_extractor(statement),
        tags=sorted({"principle", *tag_extractor(aggregate_text)}),
        metadata={
            "record_type": "adr" if is_decision else "architecture_principle",
            "verification_policy": "validated_principle" if not is_decision else "review_required",
            "principle_title": title,
            **_principle_metadata(records),
        },
    )


def _principle_source_quote(element: ElementLike, statement_value: str) -> str:
    text = (element.text_content or "").strip()
    return next((line.strip() for line in text.splitlines() if statement_value in line), text)


def _principle_statement(title: str, value: str) -> str:
    cleaned_value = value.strip().rstrip(".")
    cleaned_title = title.strip().strip("#").strip()
    if cleaned_title and cleaned_title.casefold() not in cleaned_value.casefold():
        return f"{cleaned_title} principle states {cleaned_value}."
    return f"{cleaned_value}."


def _principle_metadata(records: list[PrincipleRecord]) -> dict[str, str]:
    return {
        label: value.strip() for _element, label, value in records if label and label not in PRINCIPLE_STATEMENT_LABELS
    }
