"""Deterministic Structurizr DSL parsing for uploaded source ingestion."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StructurizrEntity:
    id: str
    name: str
    level: str


@dataclass(frozen=True, slots=True)
class StructurizrRelationship:
    source_id: str
    target_id: str
    relation_type: str
    verified: bool = True


@dataclass(frozen=True, slots=True)
class StructurizrImportResult:
    entities: tuple[StructurizrEntity, ...]
    relationships: tuple[StructurizrRelationship, ...]


_KIND_TO_LEVEL = {"softwareSystem": "system", "container": "container", "component": "component"}


def parse_structurizr_dsl(dsl: str) -> StructurizrImportResult:
    """Parse supported C4 declarations without publishing or inferring edges."""

    aliases: dict[str, StructurizrEntity] = {}
    entities: list[StructurizrEntity] = []
    relationships: list[StructurizrRelationship] = []
    for line in dsl.splitlines():
        text = line.strip()
        if not text or text.startswith("//"):
            continue
        match = re.match(r'([A-Za-z0-9_]+)\s*=\s*(softwareSystem|container|component|person)\s+"([^"]+)"', text)
        if match:
            alias, kind, name = match.groups()
            if kind == "person":
                continue
            entity = StructurizrEntity(id=f"c4_{alias}", name=name, level=_KIND_TO_LEVEL[kind])
            aliases[alias] = entity
            entities.append(entity)
            continue
        relation = re.match(r'([A-Za-z0-9_]+)\s*->\s*([A-Za-z0-9_]+)(?:\s+"([^"]+)")?', text)
        if relation:
            source, target, relation_type = relation.groups()
            if source in aliases and target in aliases:
                relationships.append(
                    StructurizrRelationship(
                        source_id=aliases[source].id,
                        target_id=aliases[target].id,
                        relation_type=(relation_type or "depends_on").strip() or "depends_on",
                    )
                )
    return StructurizrImportResult(entities=tuple(entities), relationships=tuple(relationships))
