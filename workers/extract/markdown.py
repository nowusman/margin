from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from margin_schema.extraction import (
    ExtractedClaimEvidence,
    ExtractedEntity,
    ExtractedIdeaBlock,
    ExtractedRelationship,
    ExtractorOutput,
    UnmappedExtractionCandidate,
    single_claim_evidence,
)
from margin_schema.ontology import ENTITY_KINDS, RELATIONSHIP_REGISTRY, RelationPersistence, relationship_phrase_map
from margin_schema.text_normalization import normalize_entity_name
from workers.extract.diagram_parser import parse_diagram_content
from workers.extract.markdown_frontmatter import _relationship_evidence, extract_frontmatter
from workers.extract.markdown_links import normalize_link_text
from workers.extract.markdown_names import clean_entity_name, ensure_entity, kind_for_name, owner_kind_for_name
from workers.extract.markdown_principles import extract_principle_records
from workers.extract.markdown_types import ElementLike

RELATION_PHRASES = relationship_phrase_map(persistence_class=RelationPersistence.ENTITY)
RELATION_PATTERNS = tuple(
    (re.compile(rf"^(?P<src>.+?)\s+{phrase.replace(' ', r'\s+')}\s+(?P<tgt>.+?)\.?$", re.IGNORECASE), relation_type)
    for phrase, relation_type in RELATION_PHRASES
)

STRUCTURIZR_SOFTWARE_SYSTEM_PATTERN = re.compile(r"\bsoftwareSystem\s+\"(?P<name>[^\"]+)\"", re.IGNORECASE)
STRUCTURIZR_NAMED_SOFTWARE_SYSTEM_PATTERN = re.compile(
    r"\b(?P<key>[A-Za-z][\w]*)\s*=\s*softwareSystem\s+\"(?P<name>[^\"]+)\"", re.IGNORECASE
)
STRUCTURIZR_CONTAINER_PATTERN = re.compile(r"\bcontainer\s+\"(?P<name>[^\"]+)\"", re.IGNORECASE)
STRUCTURIZR_NAMED_CONTAINER_PATTERN = re.compile(
    r"\b(?P<key>[A-Za-z][\w]*)\s*=\s*container\s+\"(?P<name>[^\"]+)\"", re.IGNORECASE
)
STRUCTURIZR_RELATIONSHIP_PATTERN = re.compile(
    r'^\s*(?P<src>[A-Za-z][\w]*)\s*->\s*(?P<tgt>[A-Za-z][\w]*)(?:\s+"(?P<label>[^"]+)")?', re.IGNORECASE
)
from workers.extract.markdown_helpers import (  # noqa: E402
    _block_type,
    _category_for_element,
    _clean_bullet,
    _element_like,
    _looks_like_complete_proposition,
    _looks_like_status_line,
    _normalize_relation,
    _statement_for_text,
    _table_int,
    _tags_for_text,
)


def _looks_like_diagram(text: str) -> bool:
    lowered = text.lstrip().casefold()
    if lowered.startswith(
        (
            "```mermaid",
            "```plantuml",
            "```structurizr",
            "```dsl",
            "@startuml",
            "flowchart ",
            "graph ",
            "workspace {",
            "softwaresystem ",
            "container ",
        )
    ):
        return True
    return bool(re.search(r"(?m)^\s*[A-Za-z][A-Za-z0-9_-]*\s*=\s*" r"(?:softwareSystem|container|component)\b", text))


def _key_value_assertion(text: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"(?P<key>[A-Za-z][A-Za-z0-9_.-]{1,80})\s*:\s*(?P<value>[^#\n]+?)\s*", text.strip())
    if match is None:
        return None
    key = match.group("key").replace("_", " ").replace("-", " ").strip()
    value = match.group("value").strip().rstrip(".")
    if not key or not value:
        return None
    return key, value


def _source_quote_for_text(element: ElementLike, text: str) -> str:
    source = element.text_content or ""
    if text in source:
        return text
    for raw_line in source.splitlines():
        display_line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", _clean_bullet(raw_line)).strip()
        if display_line == text.strip():
            return raw_line.strip()
    return text


def _with_evidence_offsets(blocks: list[ExtractedIdeaBlock], elements: list[ElementLike]) -> list[ExtractedIdeaBlock]:
    elements_by_id = {element.id: element for element in elements}
    updated: list[ExtractedIdeaBlock] = []
    for block in blocks:
        evidence: list[ExtractedClaimEvidence] = []
        changed = False
        for occurrence in block.evidence:
            element = elements_by_id.get(occurrence.source_element_id)
            if element is None or occurrence.evidence_kind != "verbatim" or occurrence.quote_char_start is not None:
                evidence.append(occurrence)
                continue
            source = element.text_content or ""
            local_start = source.find(occurrence.source_quote)
            if local_start < 0:
                evidence.append(occurrence)
                continue
            base_offset = element.char_start or 0
            evidence.append(
                occurrence.model_copy(
                    update={
                        "quote_char_start": base_offset + local_start,
                        "quote_char_end": base_offset + local_start + len(occurrence.source_quote),
                    }
                )
            )
            changed = True
        updated.append(block.model_copy(update={"evidence": evidence}) if changed else block)
    return updated


class DeterministicMarkdownExtractor:
    """Dependency-free extractor for already indexed Markdown-like elements."""

    def extract(self, *, elements: Iterable[Any], default_category: str = "Design Document") -> ExtractorOutput:
        idea_blocks: list[ExtractedIdeaBlock] = []
        element_list = [_element_like(element) for element in elements]
        principle_blocks, consumed_element_ids = extract_principle_records(
            element_list, entity_extractor=_entities_for_text, tag_extractor=_tags_for_text
        )
        idea_blocks.extend(principle_blocks)
        index = 0
        while index < len(element_list):
            element_like = element_list[index]
            if element_like.id in consumed_element_ids:
                index += 1
                continue
            text = (element_like.text_content or "").strip()
            if not text:
                index += 1
                continue
            category = _category_for_element(element_like, default_category)
            if element_like.kind in {"table", "table_header", "table_row"}:
                table_id = (element_like.metadata or {}).get("table_id")
                if element_like.kind == "table" and (element_like.metadata or {}).get("summary_only"):
                    table_elements = [
                        candidate
                        for candidate in element_list
                        if (candidate.metadata or {}).get("table_id") == table_id
                        and candidate.kind in {"table", "table_header", "table_row"}
                    ]
                    consumed_element_ids.update(candidate.id for candidate in table_elements)
                    idea_blocks.extend(_extract_table_group(table_elements, category))
                    index += 1
                    continue
                if table_id:
                    table_elements = [
                        candidate
                        for candidate in element_list
                        if (candidate.metadata or {}).get("table_id") == table_id
                        and candidate.kind in {"table", "table_header", "table_row"}
                    ]
                    consumed_element_ids.update(candidate.id for candidate in table_elements)
                    if element_like.kind != "table":
                        index += 1
                        continue
                else:
                    table_elements = [element_like]
                    index += 1
                    while index < len(element_list) and element_list[index].kind == "table":
                        table_elements.append(element_list[index])
                        index += 1
                idea_blocks.extend(_extract_table_group(table_elements, category))
                continue
            elif element_like.kind == "table_cell":
                index += 1
                continue
            elif element_like.kind == "heading":
                if _looks_like_complete_proposition(text):
                    idea_blocks.extend(_extract_text_element(element_like, category))
                index += 1
                continue
            elif element_like.kind in {"figure_caption", "table_caption", "caption", "image", "image_crop"}:
                index += 1
                continue
            elif element_like.kind in {"code", "code_block", "figure", "diagram", "mermaid", "plantuml", "structurizr"}:
                if _looks_like_diagram(text):
                    idea_blocks.extend(_extract_diagram_references(element_like, category))
                else:
                    idea_blocks.extend(_extract_text_element(element_like, category, allow_key_value=True))
            else:
                frontmatter_blocks, remaining_text = extract_frontmatter(element_like, category)
                idea_blocks.extend(frontmatter_blocks)
                if remaining_text != (element_like.text_content or ""):
                    element_like = ElementLike(
                        id=element_like.id,
                        kind=element_like.kind,
                        text_content=remaining_text,
                        structural_path=element_like.structural_path,
                        heading=element_like.heading,
                        source_page_id=element_like.source_page_id,
                        page_number=element_like.page_number,
                        metadata=element_like.metadata,
                        char_start=element_like.char_start,
                        char_end=element_like.char_end,
                        bounding_box=element_like.bounding_box,
                        asset_key=element_like.asset_key,
                        checksum=element_like.checksum,
                    )
                idea_blocks.extend(_extract_text_element(element_like, category, allow_key_value=False))
            index += 1

        return ExtractorOutput(idea_blocks=_with_evidence_offsets(idea_blocks, element_list))


def _extract_text_element(
    element: ElementLike, category: str, *, allow_key_value: bool = False
) -> list[ExtractedIdeaBlock]:
    span_text = element.text_content or ""
    lines = [_clean_bullet(line) for line in span_text.splitlines() if line.strip()]
    units: list[str] = []
    for line in (normalize_link_text(line) for line in lines):
        for sentence in re.findall(r"[^.!?]+[.!?]?", line):
            if sentence.strip():
                units.append(sentence.strip())
    blocks = [
        block
        for unit in units
        if (block := _extract_text_unit(unit, element, category, allow_key_value=allow_key_value))
    ]
    return blocks


def _extract_text_unit(
    text: str, element: ElementLike, category: str, *, allow_key_value: bool = False
) -> ExtractedIdeaBlock | None:
    if _looks_like_status_line(text):
        category = "ADR"
    block_type = _block_type(element, text)
    inline_statement = _inline_statement(text)
    statement = inline_statement or _statement_for_text(text, block_type=block_type)
    entities = _entities_for_text(statement)
    relationships = _relationships_for_text(text, entities)
    key_value = _key_value_assertion(text) if allow_key_value else None
    if not relationships and key_value is None:
        return None
    if relationships and category == "Design Document":
        block_type = "relationship"
    if key_value is not None and not relationships:
        key, value = key_value
        statement = f"{key} is {value}."
        block_type = "structured_key_value"
    parent_evidence = single_claim_evidence(
        source_element_id=element.id, source_quote=_source_quote_for_text(element, text)
    )
    if relationships:
        rel_evidence = _relationship_evidence(*parent_evidence)
        relationships = [relationship.model_copy(update={"evidence": rel_evidence}) for relationship in relationships]
    return ExtractedIdeaBlock(
        statement=statement,
        type=block_type,
        confidence="extracted",
        evidence=parent_evidence,
        extraction_method="deterministic_relationship" if relationships else "deterministic_key_value",
        entities=entities,
        relationships=relationships,
        tags=_tags_for_text(text),
        metadata={"verification_policy": "validated_relationship" if relationships else "structured_key_value"},
    )


def _table_review_block(element: ElementLike, category: str, statement: str) -> list[ExtractedIdeaBlock]:
    return [
        ExtractedIdeaBlock(
            statement=statement,
            type="table_review",
            confidence="ambiguous",
            evidence=single_claim_evidence(
                source_element_id=element.id, source_quote=(element.text_content or "").strip()
            ),
            extraction_method="deterministic_table",
            tags=["table"],
            metadata={"verification_policy": "normalized_table_assertion"},
        )
    ]


def _extract_table_group(elements: list[ElementLike], category: str) -> list[ExtractedIdeaBlock]:
    table_parent = next(
        (
            element
            for element in elements
            if element.kind == "table" and (element.metadata or {}).get("table_role") == "parent"
        ),
        None,
    )
    text = (
        table_parent.text_content
        if table_parent is not None
        else "\n".join(element.text_content or "" for element in elements)
    ) or ""
    table = _parse_markdown_table(text)
    if table is None:
        return _table_review_block(
            elements[0], category, "Table content requires review because row and column semantics were not preserved."
        )
    headers, rows = table
    structured_rows = [element for element in elements if element.kind == "table_row"]
    if structured_rows:
        row_elements = structured_rows[: len(rows)]
    elif len(elements) >= len(rows) + 2:
        row_elements = elements[2 : 2 + len(rows)]
    else:
        row_elements = [elements[0]] * len(rows)

    blocks: list[ExtractedIdeaBlock] = []
    lowered = [header.casefold() for header in headers]
    for row, row_element in zip(rows, row_elements, strict=False):
        row_map = dict(zip(lowered, row, strict=False))
        if {"risk", "severity"}.issubset(row_map):
            risk_name = row_map["risk"]
            mitigation = row_map.get("mitigation", "")
            statement = f"Risk {risk_name} has severity {row_map['severity']}."
            metadata = {
                "governance_record_type": "risk",
                "governance_title": risk_name,
                "severity": _table_int(row_map.get("severity", ""), default=3),
                "verification_policy": "normalized_table_assertion",
                **_table_provenance(row_element, row=row, headers=headers),
            }
            if mitigation:
                metadata["mitigation"] = mitigation
            if row_map.get("likelihood"):
                metadata["likelihood"] = _table_int(row_map["likelihood"], default=3)
            if row_map.get("impact"):
                metadata["impact"] = _table_int(row_map["impact"], default=3)
            if row_map.get("owner"):
                metadata["owner"] = row_map["owner"]
            if row_map.get("status"):
                metadata["lifecycle_status"] = row_map["status"]
            blocks.append(
                ExtractedIdeaBlock(
                    statement=statement,
                    type="risk_register_row",
                    confidence="extracted",
                    evidence=single_claim_evidence(
                        source_element_id=row_element.id, source_quote=_row_source_quote(row_element, row)
                    ),
                    extraction_method="deterministic_table",
                    tags=["table", "risk"],
                    metadata=metadata,
                )
            )
        elif {"system", "responsibility"}.issubset(row_map):
            system = row_map["system"]
            responsibility = row_map["responsibility"]
            blocks.append(
                ExtractedIdeaBlock(
                    statement=f"{system} is responsible for {responsibility}.",
                    type="responsibility_row",
                    confidence="extracted",
                    evidence=single_claim_evidence(
                        source_element_id=row_element.id, source_quote=_row_source_quote(row_element, row)
                    ),
                    extraction_method="deterministic_table",
                    entities=[ExtractedEntity(name=system, kind=kind_for_name(system), role="subject")],
                    tags=["table"],
                    metadata=_table_provenance(row_element, row=row, headers=headers),
                )
            )
            blocks.extend(_extract_structured_table_row(row_map, row=row, row_element=row_element, category=category))
        elif {"source", "target", "relation"}.issubset(row_map):
            source = row_map["source"]
            target = row_map["target"]
            raw_relation = row_map["relation"]
            relation = _normalize_relation(raw_relation)
            source_kind = kind_for_name(source)
            target_kind = kind_for_name(target)
            if relation is None or not _relationship_endpoint_kinds_valid(relation, source_kind, target_kind):
                blocks.append(
                    ExtractedIdeaBlock(
                        statement=f"Relationship label {raw_relation} between {source} and {target} requires review.",
                        type="interface_matrix_row",
                        confidence="ambiguous",
                        evidence=single_claim_evidence(
                            source_element_id=row_element.id, source_quote=_row_source_quote(row_element, row)
                        ),
                        extraction_method="deterministic_table",
                        entities=[
                            ExtractedEntity(name=source, kind=source_kind, role="subject"),
                            ExtractedEntity(name=target, kind=target_kind, role="object"),
                        ],
                        tags=["table", "interface", "unresolved-relationship"],
                        metadata={
                            "unresolved_relationship_label": raw_relation,
                            **_table_provenance(row_element, row=row, headers=headers),
                        },
                    )
                )
                continue
            blocks.append(
                ExtractedIdeaBlock(
                    statement=f"{source} {relation.replace('_', ' ')} {target}.",
                    type="interface_matrix_row",
                    confidence="extracted",
                    evidence=single_claim_evidence(
                        source_element_id=row_element.id, source_quote=_row_source_quote(row_element, row)
                    ),
                    extraction_method="deterministic_table",
                    entities=[
                        ExtractedEntity(name=source, kind=source_kind, role="subject"),
                        ExtractedEntity(name=target, kind=target_kind, role="object"),
                    ],
                    relationships=[
                        ExtractedRelationship(
                            source=source,
                            relation_type=relation,
                            target=target,
                            confidence="extracted",
                            evidence=_relationship_evidence(
                                ExtractedClaimEvidence(
                                    source_element_id=row_element.id, source_quote=_row_source_quote(row_element, row)
                                ),
                                extraction_method="deterministic_table",
                            ),
                        )
                    ],
                    tags=["table", "interface"],
                    metadata=_table_provenance(row_element, row=row, headers=headers),
                )
            )
        else:
            structured = _extract_structured_table_row(row_map, row=row, row_element=row_element, category=category)
            blocks.extend(structured)

    if blocks:
        return blocks
    return _table_review_block(
        elements[0], category, "Structured table was preserved but no supported row policy matched it."
    )


def _extract_diagram_references(element: ElementLike, category: str) -> list[ExtractedIdeaBlock]:
    text = (element.text_content or "").strip()
    entities: dict[str, ExtractedEntity] = {}
    relationships: list[ExtractedRelationship] = []
    quarantined_relationships: list[UnmappedExtractionCandidate] = []
    identifiers: dict[str, str] = {}

    parsed_entities, parsed_rels = parse_diagram_content(text)
    for ent in parsed_entities:
        entities[normalize_entity_name(ent.name)] = ent
    relationships.extend(parsed_rels)

    for line in text.splitlines():
        cleaned_line = _strip_code_fence(line)
        if not cleaned_line:
            continue
        for key, name, kind in _structurizr_declarations(cleaned_line):
            identifiers[key.casefold()] = name
            entities[normalize_entity_name(name)] = ExtractedEntity(name=name, kind=kind, role="subject")
        parsed_relationship = _diagram_relationship(cleaned_line, identifiers)
        if parsed_relationship is not None:
            source, relation_type, target, raw_label = parsed_relationship
            entities.setdefault(
                normalize_entity_name(source), ExtractedEntity(name=source, kind=kind_for_name(source), role="subject")
            )
            entities.setdefault(
                normalize_entity_name(target), ExtractedEntity(name=target, kind=kind_for_name(target), role="object")
            )
            if relation_type is None:
                quarantined_relationships.append(
                    UnmappedExtractionCandidate(
                        kind="relationship",
                        reason="unknown_relationship_type",
                        raw_payload={"source": source, "relation_type": raw_label, "target": target},
                    )
                )
            else:
                relationships.append(
                    ExtractedRelationship(
                        source=source,
                        relation_type=relation_type,
                        target=target,
                        confidence="extracted",
                        raw_predicate=raw_label,
                        evidence=_relationship_evidence(
                            ExtractedClaimEvidence(source_element_id=element.id, source_quote=cleaned_line),
                            extraction_method="deterministic_diagram",
                        ),
                    )
                )
        for pattern, kind in (
            (STRUCTURIZR_SOFTWARE_SYSTEM_PATTERN, "system"),
            (STRUCTURIZR_CONTAINER_PATTERN, "container"),
        ):
            match = pattern.search(cleaned_line)
            if match:
                name = match.group("name").strip()
                entities[normalize_entity_name(name)] = ExtractedEntity(name=name, kind=kind, role="subject")

    canonical_relationships: dict[tuple[str, str, str], ExtractedRelationship] = {}
    for relationship in relationships:
        source_key = normalize_entity_name(relationship.source)
        target_key = normalize_entity_name(relationship.target)
        entities.setdefault(
            source_key,
            ExtractedEntity(name=relationship.source, kind=kind_for_name(relationship.source), role="subject"),
        )
        entities.setdefault(
            target_key,
            ExtractedEntity(name=relationship.target, kind=kind_for_name(relationship.target), role="object"),
        )
        source_entity = entities[source_key]
        target_entity = entities[target_key]
        if not _relationship_endpoint_kinds_valid(relationship.relation_type, source_entity.kind, target_entity.kind):
            quarantined_relationships.append(
                UnmappedExtractionCandidate(
                    kind="relationship",
                    reason="invalid_endpoint_kinds",
                    raw_payload={
                        "source": source_entity.name,
                        "relation_type": relationship.raw_predicate or relationship.relation_type,
                        "target": target_entity.name,
                    },
                )
            )
            continue
        canonical_relationships[(source_key, relationship.relation_type, target_key)] = relationship.model_copy(
            update={"source": source_entity.name, "target": target_entity.name}
        )

    unique_unmapped = {
        (candidate.kind, candidate.reason, repr(sorted(candidate.raw_payload.items()))): candidate
        for candidate in quarantined_relationships
    }
    if not entities and not canonical_relationships and not unique_unmapped:
        return []

    blocks = [
        ExtractedIdeaBlock(
            statement=f'Diagram declares {entity.kind} "{entity.name}".',
            type="c4_entity",
            confidence="extracted",
            evidence=single_claim_evidence(
                source_element_id=element.id, source_quote=_diagram_quote(text, entity.name)
            ),
            extraction_method="deterministic_diagram",
            entities=[entity],
            tags=["diagram", "entity"],
            metadata={"verification_policy": "validated_diagram", "diagram_validated": True},
        )
        for entity in entities.values()
    ]
    for relationship in canonical_relationships.values():
        source_entity = entities[normalize_entity_name(relationship.source)]
        target_entity = entities[normalize_entity_name(relationship.target)]
        quote = _diagram_quote(text, source_entity.name, target_entity.name)
        relationship_evidence = _relationship_evidence(
            single_claim_evidence(source_element_id=element.id, source_quote=quote)[0],
            extraction_method="deterministic_diagram",
        )
        relationship_with_evidence = relationship.model_copy(
            update={"source": source_entity.name, "target": target_entity.name, "evidence": relationship_evidence}
        )
        blocks.append(
            ExtractedIdeaBlock(
                statement=(
                    f"{source_entity.name} {relationship.relation_type.replace('_', ' ')} " f"{target_entity.name}."
                ),
                type="c4_relationship",
                confidence="extracted",
                evidence=single_claim_evidence(source_element_id=element.id, source_quote=quote),
                extraction_method="deterministic_diagram",
                entities=[source_entity, target_entity],
                relationships=[relationship_with_evidence],
                tags=["diagram", "relationship"],
                metadata={"verification_policy": "validated_diagram", "diagram_validated": True},
            )
        )
    for unmapped in unique_unmapped.values():
        payload = unmapped.raw_payload
        source = str(payload.get("source", "")).strip()
        target = str(payload.get("target", "")).strip()
        raw_relation = str(payload.get("relation_type", "relationship")).strip()
        description = f"{source} {raw_relation} {target}".strip()
        statement = (
            f'Diagram relationship "{description}" requires review.'
            if description
            else "Diagram contains a relationship declaration that requires review."
        )
        blocks.append(
            ExtractedIdeaBlock(
                statement=statement,
                type="diagram_reference",
                confidence="ambiguous",
                evidence=single_claim_evidence(
                    source_element_id=element.id, source_quote=_diagram_quote(text, source, target)
                ),
                extraction_method="deterministic_diagram",
                unmapped=[unmapped],
                tags=["diagram", "review"],
                metadata={"verification_policy": "validated_diagram", "diagram_validated": False},
            )
        )
    return blocks


def _diagram_quote(text: str, *needles: str) -> str:
    normalized_needles = tuple(needle.strip().casefold() for needle in needles if needle.strip())
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line and all(needle in line.casefold() for needle in normalized_needles):
            return line
    return text


def _relationship_endpoint_kinds_valid(relation_type: str, source_kind: str, target_kind: str) -> bool:
    definition = RELATIONSHIP_REGISTRY[relation_type]
    return (
        definition.persistence_class is RelationPersistence.ENTITY
        and source_kind in definition.source_kinds
        and target_kind in definition.target_kinds
    )


def _strip_code_fence(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("```") or stripped in {"@startuml", "@enduml"}:
        return ""
    return stripped


def _structurizr_declarations(line: str) -> list[tuple[str, str, str]]:
    declarations: list[tuple[str, str, str]] = []
    for pattern, kind in (
        (STRUCTURIZR_NAMED_SOFTWARE_SYSTEM_PATTERN, "system"),
        (STRUCTURIZR_NAMED_CONTAINER_PATTERN, "container"),
    ):
        match = pattern.search(line)
        if match:
            declarations.append((match.group("key"), match.group("name").strip(), kind))
    return declarations


def _diagram_relationship(line: str, identifiers: dict[str, str]) -> tuple[str, str | None, str, str] | None:
    structurizr = STRUCTURIZR_RELATIONSHIP_PATTERN.search(line)
    if structurizr:
        raw_label = structurizr.group("label") or "depends_on"
        return (
            identifiers.get(structurizr.group("src").casefold(), structurizr.group("src")),
            _normalize_relation(raw_label),
            identifiers.get(structurizr.group("tgt").casefold(), structurizr.group("tgt")),
            raw_label,
        )

    label_match = re.search(r"\|(?P<label>[^|]+)\|", line)
    label = label_match.group("label") if label_match else None
    normalized = re.sub(r"\|[^|]*\|", " ", line)
    normalized = re.sub(r"[-.=]+(?:>|\\|/)", "-->", normalized)
    if label is None and " : " in normalized:
        normalized, label = normalized.rsplit(" : ", 1)
    match = re.search(r"(?P<src>[A-Za-z][\w .:/-]*?)\s*-+>\s*(?P<tgt>[A-Za-z][\w .:/-]*)", normalized)
    if match is None:
        return None
    source = _clean_diagram_node(match.group("src"))
    target = _clean_diagram_node(match.group("tgt"))
    if not source or not target:
        return None
    raw_label = label or "depends_on"
    return source, _normalize_relation(raw_label), target, raw_label


def _clean_diagram_node(value: str) -> str:
    cleaned = re.sub(r"[\[\](){}\"']", " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .:-")
    if " as " in cleaned.casefold():
        cleaned = cleaned.rsplit(" as ", 1)[-1].strip()
    return cleaned


def _extract_structured_table_row(
    row_map: dict[str, str], *, row: list[str], row_element: ElementLike, category: str
) -> list[ExtractedIdeaBlock]:
    subject = _first_present(
        row_map, "system", "service", "component", "entity", "api", "application", "name", "subject"
    )
    if not subject:
        return []

    blocks: list[ExtractedIdeaBlock] = []
    owner = _first_present(row_map, "owner", "team", "responsible team", "steward")
    if owner:
        ownership = _table_claim_block(
            statement=f"{subject} is owned by {owner}.",
            claim_type="owner",
            subject=subject,
            attribute="owner",
            value=owner,
            row=row,
            row_element=row_element,
            category=category,
            entity_role="owner",
            value_kind=owner_kind_for_name(owner),
        )
        blocks.append(
            ownership.model_copy(
                update={
                    "relationships": [
                        ExtractedRelationship(
                            source=subject,
                            relation_type="owned_by",
                            target=owner,
                            confidence="extracted",
                            evidence=_relationship_evidence(
                                ExtractedClaimEvidence(
                                    source_element_id=row_element.id, source_quote=_row_source_quote(row_element, row)
                                ),
                                extraction_method="deterministic_table",
                            ),
                        )
                    ]
                }
            )
        )

    status = _first_present(row_map, "status", "state", "lifecycle status")
    if status:
        blocks.append(
            _table_claim_block(
                statement=f"{subject} status is {status}.",
                claim_type="status",
                subject=subject,
                attribute="status",
                value=status,
                row=row,
                row_element=row_element,
                category=category,
            )
        )

    technology = _first_present(row_map, "technology", "database", "datastore", "platform")
    version = _first_present(row_map, "version", "api version", "contract version")
    if technology and version:
        blocks.append(
            _table_claim_block(
                statement=f"{subject} uses {technology} {version}.",
                claim_type="technology_version",
                subject=subject,
                attribute="technology_version",
                value=f"{technology} {version}",
                row=row,
                row_element=row_element,
                category=category,
                extra_metadata={"system": subject, "technology": str(technology), "version": version},
            )
        )
    elif technology:
        blocks.append(
            _table_claim_block(
                statement=f"{subject} uses {technology}.",
                claim_type="technology_choice",
                subject=subject,
                attribute="technology",
                value=technology,
                row=row,
                row_element=row_element,
                category=category,
            )
        )
    elif version:
        blocks.append(
            _table_claim_block(
                statement=f"{subject} version is {version}.",
                claim_type="version",
                subject=subject,
                attribute="version",
                value=version,
                row=row,
                row_element=row_element,
                category=category,
            )
        )

    attribute = _first_present(row_map, "attribute", "property", "field", "claim")
    value = _first_present(row_map, "value", "setting", "decision")
    if attribute and value:
        blocks.append(
            _table_claim_block(
                statement=f"{subject} {attribute} is {value}.",
                claim_type="attribute",
                subject=subject,
                attribute=attribute,
                value=value,
                row=row,
                row_element=row_element,
                category=category,
            )
        )
    return blocks


def _table_claim_block(
    *,
    statement: str,
    claim_type: str,
    subject: str,
    attribute: str,
    value: str,
    row: list[str],
    row_element: ElementLike,
    category: str,
    entity_role: str = "context",
    value_kind: str | None = None,
    extra_metadata: dict[str, str] | None = None,
) -> ExtractedIdeaBlock:
    metadata = {
        "claim_type": claim_type,
        "subject": subject,
        "attribute": attribute,
        "verification_policy": "normalized_table_assertion",
        "value": value,
        **_table_provenance(row_element, row=row),
        **(extra_metadata or {}),
    }
    return ExtractedIdeaBlock(
        statement=statement,
        type="structured_table_row",
        confidence="extracted",
        evidence=single_claim_evidence(
            source_element_id=row_element.id, source_quote=_row_source_quote(row_element, row)
        ),
        extraction_method="deterministic_table",
        entities=[
            ExtractedEntity(name=subject, kind=kind_for_name(subject), role="subject"),
            ExtractedEntity(name=value, kind=value_kind or kind_for_name(value), role=entity_role),
        ],
        metadata=metadata,
        tags=["table", claim_type],
    )


def _parse_markdown_table(text: str) -> tuple[list[str], list[list[str]]] | None:
    lines = [line.strip() for line in text.splitlines() if line.strip().startswith("|")]
    if len(lines) < 3:
        return None
    headers = _split_table_row(lines[0])
    separator = _split_table_row(lines[1])
    if not headers or not all(set(cell.replace(":", "").strip()) <= {"-"} for cell in separator):
        return None
    rows = [_split_table_row(line) for line in lines[2:]]
    if any(len(row) != len(headers) for row in rows):
        return None
    return headers, rows


def _split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _row_source_quote(row_element: ElementLike, row: list[str]) -> str:
    for line in (row_element.text_content or "").splitlines():
        if _split_table_row(line) == row:
            return line.strip()
    return (row_element.text_content or "").strip()


def _table_provenance(
    row_element: ElementLike, *, row: list[str] | None = None, headers: list[str] | None = None
) -> dict[str, object]:
    metadata = row_element.metadata or {}
    provenance: dict[str, object] = {"verification_policy": "normalized_table_assertion"}
    provenance.update(
        {
            key: metadata[key]
            for key in ("table_id", "row_index", "column_count", "column_headers")
            if metadata.get(key) is not None
        }
    )
    if row is not None and provenance:
        provenance["row_values"] = row
    if headers is not None:
        provenance["header_values"] = headers
    return provenance


def _first_present(row_map: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = row_map.get(key.casefold())
        if value and value.strip():
            return value.strip()
    return None


NEGATION_PATTERN = re.compile(r"\b(?:does not|do not|did not|no longer|is not|are not|cannot|never)\b", re.IGNORECASE)
BACKTICKED_NAME_PATTERN = re.compile(r"`([^`]+)`")


def _relationships_for_text(text: str, entities: list[ExtractedEntity]) -> list[ExtractedRelationship]:
    relationships: list[ExtractedRelationship] = []
    for line in text.splitlines():
        line = _clean_bullet(line)
        inline = _inline_relationship(line, entities)
        if inline is not None:
            relationships.append(inline)
            continue
        for pattern, relation_type in RELATION_PATTERNS:
            match = pattern.match(line)
            if not match:
                continue
            source = clean_entity_name(match.group("src"))
            target = clean_entity_name(match.group("tgt"))
            named_target = _first_named_entity(match.group("tgt"))
            if named_target:
                target = named_target
            source_kind = kind_for_name(source)
            target_kind = kind_for_name(target)
            if not _relationship_endpoint_kinds_valid(relation_type, source_kind, target_kind):
                break
            ensure_entity(entities, source, "subject", kind=source_kind)
            ensure_entity(entities, target, "object", kind=target_kind)
            relationships.append(
                ExtractedRelationship(
                    source=source,
                    relation_type=relation_type,
                    target=target,
                    confidence="extracted",
                    polarity=_polarity_for(line),
                    raw_predicate=line.strip(),
                )
            )
            break
    return relationships


def _inline_statement(text: str) -> str | None:
    """Return the proposition an inline relation states.

    An inline relation reads ``Source -> relation -> Target``, and the line
    around it may carry a label and the records' kinds. The proposition is the
    relation itself, so the surrounding words are not read as records.
    """

    parts = [part.strip() for part in text.split("->")]
    if len(parts) != 3:
        return None
    relation_type = _normalize_relation(parts[1].strip().strip("`"))
    if relation_type is None:
        return None
    source = _inline_name(parts[0])
    target = _inline_name(parts[2])
    if not source or not target:
        return None
    return f"{source} {_relation_phrase(relation_type)} {target}."


def _relation_phrase(relation_type: str) -> str:
    """Return how a relation is written in prose."""

    for phrase, candidate in RELATION_PHRASES:
        if candidate == relation_type:
            return phrase
    return relation_type.replace("_", " ")


def _inline_relationship(line: str, entities: list[ExtractedEntity]) -> ExtractedRelationship | None:
    """Read a relation stated inline, as ``Source -> relation -> Target``."""

    parts = [part.strip() for part in line.split("->")]
    if len(parts) != 3:
        return None
    relation_type = _normalize_relation(parts[1].strip().strip("`"))
    if relation_type is None:
        return None
    source = _inline_name(parts[0])
    target = _inline_name(parts[2])
    if not source or not target:
        return None
    source_kind = kind_for_name(source)
    target_kind = kind_for_name(target)
    if not _relationship_endpoint_kinds_valid(relation_type, source_kind, target_kind):
        return None
    ensure_entity(entities, source, "subject", kind=source_kind)
    ensure_entity(entities, target, "object", kind=target_kind)
    return ExtractedRelationship(
        source=source,
        relation_type=relation_type,
        target=target,
        confidence="extracted",
        polarity=_polarity_for(line),
        raw_predicate=line.strip(),
    )


def _inline_name(raw: str) -> str:
    """Return the record an inline part names.

    A part may carry a label and the record's kind before the name, as in
    ``Valid Relation: Service `Billing API```, so the record is what follows
    the label and the kind.
    """

    text = raw.strip()
    if ":" in text:
        text = text.rsplit(":", 1)[-1].strip()
    text = text.rstrip(".").strip()
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text).strip()
    backticked = BACKTICKED_NAME_PATTERN.findall(text)
    if backticked:
        return clean_entity_name(backticked[-1])
    words = text.split()
    if len(words) > 1 and words[0].casefold() in {kind.casefold() for kind in ENTITY_KINDS}:
        words = words[1:]
    return clean_entity_name(" ".join(words))


def _polarity_for(line: str) -> str:
    """Return the polarity a statement carries about a relationship."""

    return "refutes" if NEGATION_PATTERN.search(line) else "supports"


NAMED_ENTITY_PATTERN = re.compile(
    r"\b([A-Z][A-Za-z0-9]*(?:\s+[A-Z][A-Za-z0-9]*){0,3}(?:\s+(?:API|Events|Platform))?)\b"
)


def _first_named_entity(text: str) -> str:
    """Return the first record a piece of text names."""

    match = NAMED_ENTITY_PATTERN.search(text)
    return clean_entity_name(match.group(1)) if match else ""


def _entities_for_text(text: str) -> list[ExtractedEntity]:
    entities: list[ExtractedEntity] = []
    for name in NAMED_ENTITY_PATTERN.findall(text):
        cleaned = clean_entity_name(name)
        if len(cleaned) < 2 or cleaned in {"Status", "Decision", "Consequences", "Risk"}:
            continue
        ensure_entity(entities, cleaned, "context")
    for technology in ("PostgreSQL", "Redis", "MinIO", "Keycloak"):
        if re.search(rf"\b{re.escape(technology)}\b", text, re.IGNORECASE):
            ensure_entity(entities, technology, "context")
    return entities
