"""Deterministic community detection over an in-memory knowledge graph.

Community detection groups entities that are densely connected to one another
so that an employee can see which parts of a domain belong together. The
result must be reproducible: the same corpus must always resolve to the same
communities, because cached results and citations refer to community
identifiers.

The implementation is therefore deterministic by construction. Nodes are
visited in a fixed order, ties between candidate communities are broken by a
stable key, and a community identifier is derived from its final membership
rather than from the order in which the algorithm discovered it.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

from margin_graph.types import Community, GraphEdge, GraphNode

DEFAULT_RESOLUTION = 1.0
MAX_AGGREGATION_ROUNDS = 16
MAX_LOCAL_MOVING_ROUNDS = 32
_GAIN_EPSILON = 1e-12


def detect_communities(
    nodes: tuple[GraphNode, ...], edges: tuple[GraphEdge, ...], *, resolution: float = DEFAULT_RESOLUTION
) -> tuple[Community, ...]:
    """Partition the readable graph into communities.

    Only edges whose endpoints are both present in ``nodes`` are considered.
    Edges are treated as undirected for community purposes, because a
    relationship is an assertion between two entities regardless of the
    direction it was recorded in.
    """

    node_ids = tuple(sorted({node.id for node in nodes}))
    if not node_ids:
        return ()
    usable_edges = tuple(
        edge
        for edge in edges
        if edge.source_id in set(node_ids) and edge.target_id in set(node_ids) and edge.source_id != edge.target_id
    )
    if not usable_edges:
        return _isolated_communities(node_ids)

    working_ids = list(node_ids)
    working_edges = list(usable_edges)
    node_to_working: dict[str, str] = {node_id: node_id for node_id in node_ids}
    assignment: dict[str, int] = {node_id: index for index, node_id in enumerate(node_ids)}

    for _ in range(MAX_AGGREGATION_ROUNDS):
        partition = _local_moving_pass(working_ids, working_edges, resolution=resolution)
        if partition is None:
            break
        assignment = {original: partition[current] for original, current in node_to_working.items()}
        aggregated_ids, aggregated_edges, mapping = _aggregate(working_ids, working_edges, partition)
        if len(aggregated_ids) == len(working_ids):
            break
        node_to_working = {original: mapping[current] for original, current in node_to_working.items()}
        working_ids, working_edges = aggregated_ids, aggregated_edges

    return _build_communities(node_ids, usable_edges, assignment)


def _local_moving_pass(node_ids: list[str], edges: list[GraphEdge], *, resolution: float) -> dict[str, int] | None:
    """Move nodes between communities while modularity improves.

    The phase repeats until no node changes community. A single sweep is not
    enough: a node that cannot move early may become movable once its
    neighbours have settled.
    """

    adjacency: dict[str, list[tuple[str, float]]] = defaultdict(list)
    self_loop_weight: dict[str, float] = defaultdict(float)
    total_weight = 0.0
    for edge in edges:
        weight = edge.weight if edge.weight > 0 else 1.0
        total_weight += weight
        if edge.source_id == edge.target_id:
            self_loop_weight[edge.source_id] += weight
            continue
        adjacency[edge.source_id].append((edge.target_id, weight))
        adjacency[edge.target_id].append((edge.source_id, weight))
    if total_weight <= 0:
        return None

    degree = {
        node_id: 2.0 * self_loop_weight.get(node_id, 0.0) + sum(weight for _, weight in adjacency[node_id])
        for node_id in node_ids
    }
    community_of = {node_id: index for index, node_id in enumerate(node_ids)}
    members: dict[int, set[str]] = defaultdict(set)
    for node_id in node_ids:
        members[community_of[node_id]].add(node_id)
    community_degree: dict[int, float] = defaultdict(float)
    for node_id in node_ids:
        community_degree[community_of[node_id]] += degree[node_id]

    for _ in range(MAX_LOCAL_MOVING_ROUNDS):
        moved_any = False
        for node_id in node_ids:
            node_degree = degree[node_id]
            origin = community_of[node_id]
            weight_to: dict[int, float] = defaultdict(float)
            for neighbour, weight in adjacency[node_id]:
                weight_to[community_of[neighbour]] += weight

            community_degree[origin] -= node_degree
            members[origin].discard(node_id)
            origin_degree = community_degree.get(origin, 0.0)

            best_community = origin
            best_gain = _gain(
                weight_to.get(origin, 0.0), origin_degree, node_degree, total_weight, origin_degree, resolution
            )
            for candidate in sorted(weight_to):
                if candidate == origin:
                    continue
                candidate_gain = _gain(
                    weight_to[candidate],
                    community_degree.get(candidate, 0.0),
                    node_degree,
                    total_weight,
                    origin_degree,
                    resolution,
                )
                if candidate_gain > best_gain + _GAIN_EPSILON:
                    best_community, best_gain = candidate, candidate_gain
                elif abs(candidate_gain - best_gain) <= _GAIN_EPSILON and _community_key(
                    members, candidate
                ) < _community_key(members, best_community):
                    best_community, best_gain = candidate, candidate_gain

            community_of[node_id] = best_community
            members[best_community].add(node_id)
            community_degree[best_community] += node_degree
            if best_community != origin:
                moved_any = True
        if not moved_any:
            break

    return dict(community_of)


def _gain(
    internal_weight: float,
    candidate_degree: float,
    node_degree: float,
    total_weight: float,
    origin_degree: float,
    resolution: float,
) -> float:
    """Return the modularity change from moving a node into a community.

    The change in modularity when node ``i`` moves from its current community
    into a candidate community is the weight of the edges it gains, less the
    weight of the edges it leaves, scaled by the total edge weight, plus the
    change in the communities' degree totals. The resolution factor scales the
    degree term, so a higher resolution prefers smaller, tighter communities.
    """

    return (internal_weight / total_weight) + (
        resolution * (origin_degree - candidate_degree) * node_degree / (2.0 * total_weight**2)
    )


def _community_key(members: dict[int, set[str]], community_index: int) -> str:
    member_ids = members.get(community_index)
    if not member_ids:
        return f"~{community_index:09d}"
    return min(member_ids)


def _aggregate(
    node_ids: list[str], edges: list[GraphEdge], assignment: dict[str, int]
) -> tuple[list[str], list[GraphEdge], dict[str, str]]:
    """Collapse communities into super-nodes for the next aggregation round."""

    community_ids: dict[int, str] = {}
    for community_index in sorted(set(assignment.values())):
        community_ids[community_index] = f"c{community_index}"
    mapping = {node_id: community_ids[assignment[node_id]] for node_id in node_ids}
    super_set = set(mapping.values())

    weights: dict[tuple[str, str], float] = defaultdict(float)
    for edge in edges:
        source = mapping[edge.source_id]
        target = mapping[edge.target_id]
        key = (source, target) if source <= target else (target, source)
        weights[key] += edge.weight if edge.weight > 0 else 1.0

    super_edges = [
        GraphEdge(
            id=f"{source}->{target}", source_id=source, target_id=target, relation_type="aggregated", weight=weight
        )
        for (source, target), weight in sorted(weights.items())
        if source in super_set and target in super_set
    ]
    return sorted(super_set), super_edges, mapping


def _build_communities(
    node_ids: tuple[str, ...], edges: tuple[GraphEdge, ...], assignment: dict[str, int]
) -> tuple[Community, ...]:
    """Turn a partition into reported communities with cohesion metrics."""

    members_by_community: dict[int, list[str]] = defaultdict(list)
    for node_id in node_ids:
        community_index = assignment.get(node_id)
        if community_index is None:
            continue
        members_by_community[community_index].append(node_id)

    internal_count: dict[int, int] = defaultdict(int)
    boundary_count: dict[int, int] = defaultdict(int)
    internal_weight: dict[int, float] = defaultdict(float)
    boundary_weight: dict[int, float] = defaultdict(float)
    for edge in edges:
        source_community = assignment.get(edge.source_id)
        target_community = assignment.get(edge.target_id)
        if source_community is None or target_community is None:
            continue
        weight = edge.weight if edge.weight > 0 else 1.0
        if source_community == target_community:
            internal_count[source_community] += 1
            internal_weight[source_community] += weight
        else:
            boundary_count[source_community] += 1
            boundary_count[target_community] += 1
            boundary_weight[source_community] += weight
            boundary_weight[target_community] += weight

    communities: list[Community] = []
    for community_index, member_ids in sorted(members_by_community.items()):
        sorted_members = tuple(sorted(member_ids))
        total_weight = internal_weight[community_index] + boundary_weight[community_index]
        cohesion = internal_weight[community_index] / total_weight if total_weight > 0 else 0.0
        mean_intra_degree = (2.0 * internal_count[community_index] / len(sorted_members)) if sorted_members else 0.0
        communities.append(
            Community(
                community_id=_stable_community_id(sorted_members),
                member_ids=sorted_members,
                internal_edge_count=internal_count[community_index],
                boundary_edge_count=boundary_count[community_index],
                cohesion=cohesion,
                mean_intra_degree=mean_intra_degree,
            )
        )
    return tuple(sorted(communities, key=lambda community: community.community_id))


def _stable_community_id(member_ids: tuple[str, ...]) -> str:
    """Derive a community identifier from its membership alone."""

    digest = hashlib.sha256("\n".join(member_ids).encode("utf-8")).hexdigest()
    return f"community-{digest[:12]}"


def _isolated_communities(node_ids: tuple[str, ...]) -> tuple[Community, ...]:
    """Report one community per entity when the graph has no usable edges."""

    return tuple(
        Community(
            community_id=_stable_community_id((node_id,)),
            member_ids=(node_id,),
            internal_edge_count=0,
            boundary_edge_count=0,
            cohesion=0.0,
            mean_intra_degree=0.0,
        )
        for node_id in node_ids
    )
