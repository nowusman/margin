"""Knowledge graph capability surface.

This package owns the computations that turn a readable set of entities and
verified relationships into structure a person can explore: communities,
surprising connections, knowledge gaps and discoverability findings.

It is deliberately persistence-free. Authorization is resolved before a graph
is assembled, and everything here is a pure function over that assembly, so
the same corpus always produces the same answer.
"""

from __future__ import annotations

from margin_graph.community import detect_communities
from margin_graph.hygiene import detect_graph_hygiene, suggest_similar_target
from margin_graph.insights import detect_knowledge_gaps, find_surprising_connections, research_seed_for_gap
from margin_graph.types import Community, GraphEdge, GraphNode, KnowledgeGap, SurprisingConnection

__all__ = [
    "Community",
    "GraphEdge",
    "GraphNode",
    "KnowledgeGap",
    "SurprisingConnection",
    "detect_communities",
    "detect_graph_hygiene",
    "detect_knowledge_gaps",
    "find_surprising_connections",
    "research_seed_for_gap",
    "suggest_similar_target",
]
