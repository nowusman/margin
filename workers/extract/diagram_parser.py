from __future__ import annotations

import re

from margin_schema.extraction import ExtractedEntity, ExtractedRelationship
from margin_schema.ontology import RELATIONSHIP_REGISTRY, RelationPersistence, normalize_relationship_type
from margin_schema.text_normalization import normalize_entity_name
from workers.extract.markdown_names import clean_entity_name, kind_for_name

MERMAID_DIAGRAM_HEADER = re.compile(
    r"^\s*(?:graph|flowchart|classDiagram|sequenceDiagram|erDiagram|stateDiagram)", re.IGNORECASE
)
_RELATIONSHIP_ARROW_PATTERN = re.compile(
    r"^\s*(?P<src>[A-Za-z0-9_\-\s\"']+?)\s*"
    r"(?P<arrow>-\.->|-->|==>|->>|--\|>|->)\s*"
    r"(?:\s*\"(?P<label>[^\"]+)\"\s*|:(?P<colon_label>[^\n]+)\s*)?\s*"
    r"(?P<tgt>[A-Za-z0-9_\-\s\"']+?)\s*$"
)

_MERMAID_NODE_PATTERN = re.compile(
    r"^\s*(?P<id>[A-Za-z0-9_\-]+)\s*"
    r"(?:\[(?P<box_label>[^\]]+)\]|\((?P<round_label>[^\)]+)\)|\{(?P<rhombus_label>[^\}]+)\})"
)

_PLANTUML_NODE_PATTERN = re.compile(
    r"^\s*(?:component|node|package|rectangle|cloud|database|actor|usecase)\s+"
    r"(?:\"(?P<label>[^\"]+)\"\s+as\s+)?(?P<id>[A-Za-z0-9_\-]+)"
)


_STRUCTURIZR_REL_PATTERN = re.compile(
    r'^\s*(?P<src>[A-Za-z0-9_\-]+)\s*->\s*(?P<tgt>[A-Za-z0-9_\-]+)(?:\s+"(?P<label>[^"]+)")?\s*$'
)


def parse_diagram_content(text: str) -> tuple[list[ExtractedEntity], list[ExtractedRelationship]]:
    entities: dict[str, ExtractedEntity] = {}
    relationships: list[ExtractedRelationship] = []

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return [], []

    for line in lines:
        if line.startswith("```") or line.startswith("@") or MERMAID_DIAGRAM_HEADER.match(line):
            continue

        struct_match = _STRUCTURIZR_REL_PATTERN.match(line)
        if struct_match:
            raw_src = struct_match.group("src")
            raw_tgt = struct_match.group("tgt")
            label = struct_match.group("label")
            src_clean = clean_entity_name(raw_src)
            tgt_clean = clean_entity_name(raw_tgt)
            if src_clean and tgt_clean and normalize_entity_name(src_clean) != normalize_entity_name(tgt_clean):
                entities.setdefault(
                    normalize_entity_name(src_clean),
                    ExtractedEntity(name=src_clean, kind=kind_for_name(src_clean), role="subject"),
                )
                entities.setdefault(
                    normalize_entity_name(tgt_clean),
                    ExtractedEntity(name=tgt_clean, kind=kind_for_name(tgt_clean), role="object"),
                )
                norm_rel = _entity_relationship_type(
                    normalize_relationship_type(label) if label else "depends_on",
                    kind_for_name(src_clean),
                    kind_for_name(tgt_clean),
                )
                if norm_rel is not None:
                    relationships.append(
                        ExtractedRelationship(
                            source=src_clean, relation_type=norm_rel, target=tgt_clean, confidence="extracted"
                        )
                    )
            continue

        mermaid_node_match = _MERMAID_NODE_PATTERN.match(line)
        if mermaid_node_match:
            node_id = mermaid_node_match.group("id")
            label = (
                mermaid_node_match.group("box_label")
                or mermaid_node_match.group("round_label")
                or mermaid_node_match.group("rhombus_label")
                or node_id
            )
            cleaned = clean_entity_name(label)
            if cleaned:
                entities[normalize_entity_name(cleaned)] = ExtractedEntity(
                    name=cleaned, kind=kind_for_name(cleaned), role="subject"
                )

        plantuml_node_match = _PLANTUML_NODE_PATTERN.match(line)
        if plantuml_node_match:
            label = plantuml_node_match.group("label") or plantuml_node_match.group("id")
            cleaned = clean_entity_name(label)
            if cleaned:
                entities[normalize_entity_name(cleaned)] = ExtractedEntity(
                    name=cleaned, kind=kind_for_name(cleaned), role="subject"
                )

        arrow_match = _RELATIONSHIP_ARROW_PATTERN.match(line)
        if arrow_match:
            raw_src = arrow_match.group("src").strip(" \"'")
            raw_tgt = arrow_match.group("tgt").strip(" \"'")
            arrow = arrow_match.group("arrow")
            src_clean = clean_entity_name(raw_src)
            tgt_clean = clean_entity_name(raw_tgt)

            if src_clean and tgt_clean and normalize_entity_name(src_clean) != normalize_entity_name(tgt_clean):
                entities.setdefault(
                    normalize_entity_name(src_clean),
                    ExtractedEntity(name=src_clean, kind=kind_for_name(src_clean), role="subject"),
                )
                entities.setdefault(
                    normalize_entity_name(tgt_clean),
                    ExtractedEntity(name=tgt_clean, kind=kind_for_name(tgt_clean), role="object"),
                )

                if arrow == "-.->":
                    continue
                rel_type = "contains" if arrow in ("==>", "--|>") else "depends_on"

                valid_relation = _entity_relationship_type(rel_type, kind_for_name(src_clean), kind_for_name(tgt_clean))
                if valid_relation is not None:
                    relationships.append(
                        ExtractedRelationship(
                            source=src_clean, relation_type=valid_relation, target=tgt_clean, confidence="extracted"
                        )
                    )

    return list(entities.values()), relationships


def _entity_relationship_type(relation_type: str | None, source_kind: str, target_kind: str) -> str | None:
    if relation_type is None:
        return None
    definition = RELATIONSHIP_REGISTRY.get(relation_type)
    if definition is None or definition.persistence_class is not RelationPersistence.ENTITY:
        return None
    if source_kind not in definition.source_kinds or target_kind not in definition.target_kinds:
        return None
    return definition.name
