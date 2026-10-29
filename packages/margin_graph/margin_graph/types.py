"""Value types for the knowledge graph capability surface.

The graph capability is deliberately persistence-free: it operates on an
in-memory projection of readable entities and verified relationships that the
authorization layer has already assembled. Nothing in this package reads or
writes the operational system of record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True, slots=True)
class GraphNode:
    """A readable entity participating in a knowledge subgraph."""

    id: str
    label: str
    kind: str
    domain_id: str | None = None
    definition: str | None = None
    ontology_version: str | None = None


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """A relationship between two entities, with its justifying evidence."""

    id: str
    source_id: str
    target_id: str
    relation_type: str
    confidence: str = "medium"
    status: str = "verified"
    valid_from: date | None = None
    valid_to: date | None = None
    quotes: tuple[str, ...] = ()
    idea_block_ids: tuple[str, ...] = ()
    weight: float = 1.0


@dataclass(frozen=True, slots=True)
class Community:
    """A detected cluster of entities that belong together."""

    community_id: str
    member_ids: tuple[str, ...]
    internal_edge_count: int
    boundary_edge_count: int
    cohesion: float
    mean_intra_degree: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "community_id": self.community_id,
            "member_ids": list(self.member_ids),
            "internal_edge_count": self.internal_edge_count,
            "boundary_edge_count": self.boundary_edge_count,
            "cohesion": round(self.cohesion, 6),
            "mean_intra_degree": round(self.mean_intra_degree, 6),
        }


@dataclass(frozen=True, slots=True)
class SurprisingConnection:
    """A connection between records that usually sit apart."""

    edge_id: str
    source_id: str
    target_id: str
    score: int
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "score": self.score,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class KnowledgeGap:
    """A structural weakness in the knowledge graph."""

    gap_type: str
    title: str
    description: str
    node_ids: tuple[str, ...]
    suggestion: str
    research_term: str | None = None
    research_context: str | None = None
    subject: str = ""
    """What the gap is about, when the type alone does not identify it.

    The subject is what makes a gap addressable: the records a gap covers
    change as the corpus grows, so they cannot identify it. A decision about
    one gap must not silently start applying to another.
    """

    def as_dict(self) -> dict[str, Any]:
        return {
            "gap_type": self.gap_type,
            "title": self.title,
            "description": self.description,
            "node_ids": list(self.node_ids),
            "suggestion": self.suggestion,
            "research_term": self.research_term,
            "research_context": self.research_context,
        }
