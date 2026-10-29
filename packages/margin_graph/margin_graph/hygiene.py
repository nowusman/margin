"""Discoverability rules for the readable knowledge graph.

The existing hygiene lint asks whether a record is internally sound: does it
reference records that exist, does it stay inside its domain, does it carry
evidence. These rules ask a different question: can a reader find it, and can
they get anywhere from it.

A record with no connections is stranded. A record with no onward connections
is a dead end. A relationship pointing at a record that is no longer active is
a broken reference. None of these make a record wrong, but each of them makes
the graph harder to navigate, and each is cheap to report.
"""

from __future__ import annotations

import re
from collections import defaultdict

from margin_graph.types import GraphEdge, GraphNode
from margin_schema.findings import HygieneFinding

ORPHAN_RULE_ID = "graph_orphan_record"
DEAD_END_RULE_ID = "graph_dead_end_record"
BROKEN_REFERENCE_RULE_ID = "graph_broken_reference"
SUGGESTION_MINIMUM_SCORE = 0.6
_SUGGESTION_CONTAINMENT_SCORE = 0.85
_NORMALIZE_PATTERN = re.compile(r"[^a-z0-9]+")


def detect_graph_hygiene(nodes: tuple[GraphNode, ...], edges: tuple[GraphEdge, ...]) -> tuple[HygieneFinding, ...]:
    """Report stranded records, dead ends and broken references."""

    findings: list[HygieneFinding] = []
    node_by_id = {node.id: node for node in nodes}
    incident: dict[str, int] = defaultdict(int)
    outgoing: dict[str, int] = defaultdict(int)
    incoming: dict[str, int] = defaultdict(int)

    for edge in edges:
        incident[edge.source_id] += 1
        incident[edge.target_id] += 1
        outgoing[edge.source_id] += 1
        incoming[edge.target_id] += 1

    for node in sorted(nodes, key=lambda item: item.id):
        degree = incident.get(node.id, 0)
        if degree == 0:
            findings.append(
                HygieneFinding(
                    rule_id=ORPHAN_RULE_ID,
                    severity="info",
                    target_type="entity",
                    target_id=node.id,
                    message=f"'{node.label}' is not connected to any other record.",
                    recommendation=(
                        "Record how this record relates to the rest of the domain, or confirm that "
                        "it is intentionally standalone."
                    ),
                    evidence=({"entity_id": node.id, "kind": node.kind, "degree": "0"},),
                )
            )
            continue
        if outgoing.get(node.id, 0) == 0 and incoming.get(node.id, 0) > 0:
            findings.append(
                HygieneFinding(
                    rule_id=DEAD_END_RULE_ID,
                    severity="info",
                    target_type="entity",
                    target_id=node.id,
                    message=f"'{node.label}' has no onward relationships.",
                    recommendation=(
                        "Add the relationships that lead away from this record so a reader can "
                        "continue exploring from it."
                    ),
                    evidence=({"entity_id": node.id, "kind": node.kind, "incoming": str(incoming.get(node.id, 0))},),
                )
            )

    for edge in sorted(edges, key=lambda item: item.id):
        missing_endpoint = None
        if edge.source_id not in node_by_id:
            missing_endpoint = edge.source_id
        elif edge.target_id not in node_by_id:
            missing_endpoint = edge.target_id
        if missing_endpoint is None:
            continue
        suggestion = suggest_similar_target(missing_endpoint, nodes)
        findings.append(
            HygieneFinding(
                rule_id=BROKEN_REFERENCE_RULE_ID,
                severity="warning",
                target_type="entity_relationship",
                target_id=edge.id,
                message=(
                    f"Relationship '{edge.relation_type}' points at '{missing_endpoint}', "
                    "which is not an active record."
                ),
                recommendation=(
                    f"Repoint the relationship at '{suggestion}'."
                    if suggestion
                    else "Repoint the relationship at an active record, or withdraw it."
                ),
                evidence=(
                    {
                        "relationship_id": edge.id,
                        "relation_type": edge.relation_type,
                        "missing_entity_id": missing_endpoint,
                        "suggested_entity_id": suggestion or "",
                    },
                ),
            )
        )

    return tuple(findings)


def suggest_similar_target(identifier: str, nodes: tuple[GraphNode, ...]) -> str | None:
    """Return the most similar active record for a missing reference."""

    target = _normalize(identifier)
    if not target:
        return None
    best_id: str | None = None
    best_score = 0.0
    for node in sorted(nodes, key=lambda item: item.id):
        label = _normalize(node.label)
        if not label:
            continue
        if label == target:
            return node.id
        score = _similarity(target, label)
        if score > best_score:
            best_id, best_score = node.id, score
    if best_id is not None and best_score >= SUGGESTION_MINIMUM_SCORE:
        return best_id
    return None


def _similarity(left: str, right: str) -> float:
    """Return a bounded similarity score between two normalized names."""

    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left in right or right in left:
        return _SUGGESTION_CONTAINMENT_SCORE
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    if not overlap:
        return 0.0
    union = len(left_tokens | right_tokens)
    return overlap / union


def _normalize(value: str) -> str:
    return _NORMALIZE_PATTERN.sub(" ", value.lower()).strip()
