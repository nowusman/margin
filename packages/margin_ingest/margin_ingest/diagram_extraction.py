"""Deterministic candidate extraction from uploaded architecture diagrams."""

from __future__ import annotations

from margin_ingest.structurizr import StructurizrEntity, StructurizrRelationship
from margin_schema.extraction import (
    ExtractedEntity,
    ExtractedIdeaBlock,
    ExtractedRelationship,
    ExtractorOutput,
    UnmappedExtractionCandidate,
    single_claim_evidence,
)
from margin_schema.ontology import normalize_relationship_type


def diagram_output(
    *,
    relative_path: str,
    diagram_type: str,
    source_element_id: str,
    entities: tuple[StructurizrEntity, ...],
    relationships: tuple[StructurizrRelationship, ...],
    markup: str,
) -> ExtractorOutput:
    """Build citable candidate assertions from one uploaded diagram source."""

    if diagram_type != "structurizr":
        return ExtractorOutput(
            idea_blocks=[
                ExtractedIdeaBlock(
                    statement=f"Diagram {relative_path} is a {diagram_type} architecture diagram.",
                    type="diagram_source",
                    confidence="extracted",
                    evidence=single_claim_evidence(
                        source_element_id=source_element_id, source_quote=_quote(markup, relative_path)
                    ),
                    extraction_method="deterministic_diagram",
                    tags=["diagram", diagram_type],
                    metadata={
                        "diagram_validated": True,
                        "relative_path": relative_path,
                        "verification_policy": "validated_diagram",
                    },
                )
            ]
        )

    blocks: list[ExtractedIdeaBlock] = []
    entities_by_id = {entity.id: entity for entity in entities}
    for entity in entities:
        blocks.append(
            ExtractedIdeaBlock(
                statement=f"{entity.name} is represented as a C4 {entity.level} in {relative_path}.",
                type="c4_entity",
                confidence="extracted",
                evidence=single_claim_evidence(
                    source_element_id=source_element_id, source_quote=_quote(markup, entity.name)
                ),
                extraction_method="deterministic_diagram",
                entities=[ExtractedEntity(name=entity.name, kind=_entity_kind(entity.level), role="subject")],
                tags=["diagram", "structurizr", "c4"],
                metadata={
                    "c4_level": entity.level,
                    "diagram_validated": True,
                    "relative_path": relative_path,
                    "verification_policy": "validated_diagram",
                },
            )
        )
    for relationship in relationships:
        source = entities_by_id.get(relationship.source_id)
        target = entities_by_id.get(relationship.target_id)
        if source is None or target is None:
            continue
        relation_type = normalize_relationship_type(relationship.relation_type)
        blocks.append(
            ExtractedIdeaBlock(
                statement=(
                    f"{source.name} {(relation_type or relationship.relation_type).replace('_', ' ')} "
                    f"{target.name}."
                ),
                type="c4_relationship",
                confidence="extracted",
                evidence=single_claim_evidence(
                    source_element_id=source_element_id, source_quote=_quote(markup, source.name)
                ),
                extraction_method="deterministic_diagram",
                entities=[
                    ExtractedEntity(name=source.name, kind=_entity_kind(source.level), role="subject"),
                    ExtractedEntity(name=target.name, kind=_entity_kind(target.level), role="object"),
                ],
                relationships=(
                    [
                        ExtractedRelationship(
                            source=source.name, relation_type=relation_type, target=target.name, confidence="extracted"
                        )
                    ]
                    if relation_type is not None
                    else []
                ),
                unmapped=(
                    []
                    if relation_type is not None
                    else [
                        UnmappedExtractionCandidate(
                            kind="relationship",
                            reason="unknown_relationship_type",
                            raw_payload={
                                "relation_type": relationship.relation_type,
                                "source": source.name,
                                "target": target.name,
                            },
                        )
                    ]
                ),
                tags=["diagram", "structurizr", "c4"],
                metadata={
                    "diagram_validated": True,
                    "original_relation_type": relationship.relation_type,
                    "relative_path": relative_path,
                    "verification_policy": "validated_diagram",
                },
            )
        )
    return ExtractorOutput(idea_blocks=blocks)


def _entity_kind(level: str) -> str:
    return level if level in {"system", "container", "component"} else "component"


def _quote(markup: str, hint: str) -> str:
    for line in markup.splitlines():
        if hint in line:
            return line.strip()
    return markup.strip().splitlines()[0] if markup.strip() else hint
