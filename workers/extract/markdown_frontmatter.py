from __future__ import annotations

import re

from margin_schema.extraction import (
    ExtractedClaimEvidence,
    ExtractedEntity,
    ExtractedIdeaBlock,
    ExtractedRelationshipEvidence,
)
from workers.extract.markdown_names import kind_for_name
from workers.extract.markdown_types import ElementLike, FrontmatterField

FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(?P<body>.*?)(?:\n---\s*)(?:\n|$)", re.DOTALL)


def extract_frontmatter(element: ElementLike, category: str) -> tuple[list[ExtractedIdeaBlock], str]:
    text = element.text_content or ""
    match = FRONTMATTER_PATTERN.match(text)
    if not match:
        return [], text

    fields = _parse_simple_yaml(match.group("body"))
    remaining = text[match.end() :].strip()
    if not fields:
        return [], remaining

    field_values = {key: field.value for key, field in fields.items()}
    resolved_category = _frontmatter_category(field_values, element, category)
    governance_record_type = _governance_record_type_for_category(resolved_category)
    governance_metadata = _frontmatter_governance_metadata(field_values, governance_record_type)
    blocks: list[ExtractedIdeaBlock] = []
    for key, field in fields.items():
        if key in {"tags", "aliases"}:
            continue
        value = field.value
        statement = _frontmatter_statement(key, value)
        if statement is None:
            continue
        detail_metadata = governance_metadata if key in {"title", "name"} else {}
        fm_entities: list[ExtractedEntity] = [*_frontmatter_entities(key, value)]

        blocks.append(
            ExtractedIdeaBlock(
                statement=statement,
                type=f"frontmatter_{key}",
                confidence="extracted",
                evidence=[
                    ExtractedClaimEvidence(source_element_id=element.id, source_quote=f"{field.raw_key}: {value}")
                ],
                entities=fm_entities,
                tags=["frontmatter", *_frontmatter_tags(field_values)],
                metadata={
                    "record_type": "frontmatter",
                    "frontmatter_key": key,
                    "frontmatter_value": value,
                    "verification_policy": "structured_key_value",
                    **detail_metadata,
                },
                extraction_method="deterministic_key_value",
            )
        )
    return blocks, remaining


def _relationship_evidence(
    *evidence: ExtractedClaimEvidence, extraction_method: str | None = None
) -> list[ExtractedRelationshipEvidence]:
    """Carry source-backed claim evidence onto a binary relationship."""

    return [
        ExtractedRelationshipEvidence(
            source_element_id=item.source_element_id,
            source_quote=item.source_quote,
            polarity=item.polarity,
            qualifiers=item.qualifiers,
            valid_from=item.valid_from,
            valid_to=item.valid_to,
            extraction_method=extraction_method,
        )
        for item in evidence
    ]


def _parse_simple_yaml(text: str) -> dict[str, FrontmatterField]:
    fields: dict[str, FrontmatterField] = {}
    current_key: str | None = None
    current_raw_key: str | None = None
    current_values: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t", "-")) and current_key:
            current_values.append(line.strip().lstrip("-").strip().strip("'\""))
            fields[current_key] = FrontmatterField(
                raw_key=current_raw_key or current_key, value=", ".join(value for value in current_values if value)
            )
            continue
        if ":" not in line:
            current_key = None
            current_raw_key = None
            current_values = []
            continue
        key, value = line.split(":", 1)
        current_raw_key = key.strip()
        current_key = current_raw_key.casefold().replace("-", "_")
        current_values = [value.strip().strip("'\"")]
        if current_key:
            fields[current_key] = FrontmatterField(raw_key=current_raw_key, value=current_values[0])
    return {key: field for key, field in fields.items() if field.value}


def _frontmatter_category(fields: dict[str, str], element: ElementLike | None, fallback: str) -> str:
    type_hint = " ".join(str(fields.get(key, "")) for key in ("type", "category", "status")).casefold()
    path = ((element.structural_path if element else "") or "").casefold()
    if "adr" in type_hint or "madr" in type_hint or "adr" in path:
        return "ADR"
    if "risk" in type_hint:
        return "Risk"
    if "waiver" in type_hint:
        return "Waiver"
    if "debt" in type_hint:
        return "Technical Debt"
    if fallback in {
        "ADR",
        "Risk",
        "Waiver",
        "Technical Debt",
        "Architecture Backlog",
        "Architecture Standards",
        "Architecture Principles",
        "Research Work",
    }:
        return fallback
    return "Design Document"


def _frontmatter_statement(key: str, value: str) -> str | None:
    label = key.replace("_", " ")
    if key in {"title", "name"}:
        return f"Document title is {value}."
    if key in {"status", "decision_status"}:
        return f"ADR status is {value}."
    if key in {"date", "decision_date"}:
        return f"Decision date is {value}."
    if key in {"owner", "steward", "reviewer", "approver"}:
        return f"Document {label} is {value}."
    if key in {"supersedes", "superseded_by"}:
        return f"ADR {label} {value}."
    if key in {"system", "domain", "service", "component"}:
        return f"Document {label} is {value}."
    return None


def _frontmatter_entities(key: str, value: str) -> list[ExtractedEntity]:
    if key in {"owner", "steward", "reviewer", "approver"}:
        return []
    if key in {"system", "service", "component"}:
        return [ExtractedEntity(name=value, kind=kind_for_name(value), role="subject")]
    if key == "domain":
        return [ExtractedEntity(name=value, kind="domain", role="context")]
    return []


def _frontmatter_governance_metadata(fields: dict[str, str], governance_record_type: str | None) -> dict[str, str]:
    if governance_record_type is None:
        return {}
    metadata = {
        "governance_record_type": governance_record_type,
        "governance_title": fields.get("title") or fields.get("name") or "",
    }
    if governance_record_type == "adr":
        _copy_frontmatter_metadata(fields, metadata, {"status": "decision_status", "date": "decision_date"})
    elif governance_record_type in {"risk", "waiver"}:
        _copy_frontmatter_metadata(fields, metadata, {"status": "lifecycle_status", "owner": "owner"})
    elif governance_record_type == "technical_debt":
        _copy_frontmatter_metadata(fields, metadata, {"status": "lifecycle_status", "type": "debt_type"})
    _copy_frontmatter_metadata(
        fields,
        metadata,
        {
            "decision_status": "decision_status",
            "decision_date": "decision_date",
            "standard_ref": "standard_ref",
            "approver": "approver",
            "reason": "reason",
            "expiry_date": "expiry_date",
            "severity": "severity",
            "likelihood": "likelihood",
            "impact": "impact",
            "mitigation": "mitigation",
            "estimated_effort_days": "estimated_effort_days",
            "interest_score": "interest_score",
            "target_resolution_date": "target_resolution_date",
        },
    )
    return {key: value for key, value in metadata.items() if value}


def _copy_frontmatter_metadata(fields: dict[str, str], metadata: dict[str, str], mapping: dict[str, str]) -> None:
    for source_key, target_key in mapping.items():
        if fields.get(source_key):
            metadata[target_key] = fields[source_key]


def _governance_record_type_for_category(category: str) -> str | None:
    return {"ADR": "adr", "Risk": "risk", "Waiver": "waiver", "Technical Debt": "technical_debt"}.get(category)


def _frontmatter_tags(fields: dict[str, str]) -> list[str]:
    value = fields.get("tags", "")
    return [tag.strip().casefold() for tag in value.split(",") if tag.strip()]
