"""Insight scoring over the readable knowledge graph.

Two questions are answered here. Which connections between records are
surprising, because they cross a boundary that a reader would not expect? And
where is the graph thin, because a record is isolated, a cluster is loosely
held together, or one record carries several areas on its own?

Both answers are structural observations about records that already exist. They
are prompts for a reader, not claims about the world, so every result carries
the reason it was produced.
"""

from __future__ import annotations

from collections import defaultdict

from margin_graph.types import Community, GraphEdge, GraphNode, KnowledgeGap, SurprisingConnection

MINIMUM_SURPRISE_SCORE = 3
DEFAULT_SURPRISE_LIMIT = 5
DEFAULT_GAP_LIMIT = 8
ISOLATED_NODE_DEGREE = 1
SPARSE_COMMUNITY_MEAN_DEGREE = 2.0
SPARSE_COMMUNITY_MINIMUM_MEMBERS = 3
BRIDGE_COMMUNITY_COUNT = 3

_KIND_FAMILIES: dict[str, str] = {
    "source": "evidence",
    "evidence": "evidence",
    "source_element": "evidence",
    "concept": "taxonomy",
    "category": "taxonomy",
    "tag": "taxonomy",
    "claim": "analysis",
    "idea_block": "analysis",
    "synthesis": "analysis",
    "finding": "analysis",
    "principle": "analysis",
    "standard": "governance",
    "policy": "governance",
    "control": "governance",
    "requirement": "governance",
    "process": "organisation",
    "business_capability": "organisation",
    "value_stream": "organisation",
    "person": "actor",
    "team": "actor",
    "organization": "actor",
    "role": "actor",
    "system": "structure",
    "container": "structure",
    "component": "structure",
    "api": "structure",
    "data_store": "structure",
    "technology_asset": "structure",
    "deployment": "structure",
    "environment": "structure",
    "query": "question",
}


def find_surprising_connections(
    nodes: tuple[GraphNode, ...],
    edges: tuple[GraphEdge, ...],
    communities: tuple[Community, ...],
    *,
    limit: int = DEFAULT_SURPRISE_LIMIT,
) -> tuple[SurprisingConnection, ...]:
    """Rank connections that cross a boundary a reader would not expect."""

    if not nodes or not edges:
        return ()
    node_by_id = {node.id: node for node in nodes}
    community_by_member: dict[str, str] = {}
    for community in communities:
        for member_id in community.member_ids:
            community_by_member[member_id] = community.community_id
    degree_by_node: dict[str, int] = defaultdict(int)
    for edge in edges:
        degree_by_node[edge.source_id] += 1
        degree_by_node[edge.target_id] += 1
    if not degree_by_node:
        return ()
    maximum_degree = max(degree_by_node.values())

    scored: list[SurprisingConnection] = []
    for edge in edges:
        source = node_by_id.get(edge.source_id)
        target = node_by_id.get(edge.target_id)
        if source is None or target is None:
            continue
        score = 0
        reasons: list[str] = []

        source_community = community_by_member.get(source.id)
        target_community = community_by_member.get(target.id)
        if source_community and target_community and source_community != target_community:
            score += 3
            reasons.append("crosses community boundary")

        if source.kind != target.kind:
            if _kind_family(source.kind) != _kind_family(target.kind):
                score += 2
                reasons.append(f"connects {source.kind} to {target.kind}")
            else:
                score += 1
                reasons.append("connects different kinds within the same family")

        source_degree = degree_by_node.get(source.id, 0)
        target_degree = degree_by_node.get(target.id, 0)
        lower_degree = min(source_degree, target_degree)
        higher_degree = max(source_degree, target_degree)
        if lower_degree <= ISOLATED_NODE_DEGREE and higher_degree >= maximum_degree * 0.5:
            score += 2
            reasons.append("peripheral record links to a hub")

        if 0 < edge.weight < 2:
            score += 1
            reasons.append("weak but present connection")

        if score >= MINIMUM_SURPRISE_SCORE and reasons:
            scored.append(
                SurprisingConnection(
                    edge_id=edge.id, source_id=source.id, target_id=target.id, score=score, reasons=tuple(reasons)
                )
            )

    scored.sort(key=lambda connection: (-connection.score, connection.edge_id))
    return tuple(scored[:limit])


def detect_knowledge_gaps(
    nodes: tuple[GraphNode, ...],
    edges: tuple[GraphEdge, ...],
    communities: tuple[Community, ...],
    *,
    limit: int = DEFAULT_GAP_LIMIT,
) -> tuple[KnowledgeGap, ...]:
    """Detect isolated records, sparse clusters and load-bearing records."""

    if not nodes:
        return ()
    node_by_id = {node.id: node for node in nodes}
    degree_by_node: dict[str, int] = defaultdict(int)
    for edge in edges:
        degree_by_node[edge.source_id] += 1
        degree_by_node[edge.target_id] += 1

    gaps: list[KnowledgeGap] = []

    isolated = sorted(node.id for node in nodes if degree_by_node.get(node.id, 0) <= ISOLATED_NODE_DEGREE)
    if isolated:
        labels = [node_by_id[node_id].label for node_id in isolated if node_id in node_by_id]
        gaps.append(
            KnowledgeGap(
                gap_type="isolated-record",
                title=f"{len(isolated)} isolated record{'s' if len(isolated) > 1 else ''}",
                description=", ".join(labels[:5]) + (f" and {len(labels) - 5} more" if len(labels) > 5 else ""),
                node_ids=tuple(isolated),
                subject="",
                suggestion=(
                    "These records have few or no connections. Connect them to related records "
                    "or extend the source material that describes them."
                ),
            )
        )

    for community in communities:
        if community.mean_intra_degree < SPARSE_COMMUNITY_MEAN_DEGREE and len(community.member_ids) >= (
            SPARSE_COMMUNITY_MINIMUM_MEMBERS
        ):
            gaps.append(
                KnowledgeGap(
                    gap_type="sparse-cluster",
                    title=f"Sparse cluster of {len(community.member_ids)} records",
                    description=(
                        f"Records in this cluster average {community.mean_intra_degree:.1f} internal "
                        "connections each."
                    ),
                    node_ids=community.member_ids,
                    subject=community.community_id,
                    suggestion=(
                        "This area lacks internal cross-references. Record how these records relate "
                        "to one another, or extend the source material."
                    ),
                )
            )

    community_by_member: dict[str, str] = {}
    for community in communities:
        for member_id in community.member_ids:
            community_by_member[member_id] = community.community_id
    neighbour_communities: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        source_community = community_by_member.get(edge.source_id)
        target_community = community_by_member.get(edge.target_id)
        if source_community is None or target_community is None:
            continue
        neighbour_communities[edge.source_id].add(target_community)
        neighbour_communities[edge.target_id].add(source_community)

    bridge_ids = sorted(
        (node.id for node in nodes if len(neighbour_communities.get(node.id, set())) >= BRIDGE_COMMUNITY_COUNT),
        key=lambda node_id: (-len(neighbour_communities.get(node_id, set())), node_id),
    )
    for node_id in bridge_ids[:3]:
        node = node_by_id.get(node_id)
        if node is None:
            continue
        community_count = len(neighbour_communities.get(node_id, set()))
        gaps.append(
            KnowledgeGap(
                gap_type="bridge-record",
                title=f"Bridge record: {node.label}",
                description=f"Connects {community_count} separate areas of the graph.",
                node_ids=(node_id,),
                subject=node_id,
                suggestion=(
                    "This record carries several areas at once. Keep it current, because a thin "
                    "record here weakens every area it touches."
                ),
            )
        )

    return tuple(gaps[:limit])


def research_seed_for_gap(gap: KnowledgeGap, nodes: tuple[GraphNode, ...]) -> tuple[str, str]:
    """Turn a structural gap into a proposed investigation."""

    node_by_id = {node.id: node for node in nodes}
    candidates = [node_by_id[node_id] for node_id in gap.node_ids if node_id in node_by_id]
    if not candidates:
        return gap.title, ""
    if gap.gap_type == "isolated-record":
        return candidates[0].label, ""
    ordered = sorted(candidates, key=lambda node: node.label)
    representative = ordered[0].label
    context = " ".join(node.label for node in ordered[1:3])
    return representative, context


def _kind_family(kind: str) -> str:
    """Return the family a kind belongs to, so distant kinds can be recognised."""

    return _KIND_FAMILIES.get(kind, "structure")
