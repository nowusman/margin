from __future__ import annotations

from margin_graph import (
    detect_communities,
    detect_graph_hygiene,
    detect_knowledge_gaps,
    find_surprising_connections,
    research_seed_for_gap,
)
from margin_graph.hygiene import BROKEN_REFERENCE_RULE_ID, DEAD_END_RULE_ID, ORPHAN_RULE_ID
from margin_graph.types import GraphEdge, GraphNode


def _node(identifier: str, label: str, kind: str) -> GraphNode:
    return GraphNode(id=identifier, label=label, kind=kind)


def _edge(identifier: str, source: str, target: str, relation: str = "uses") -> GraphEdge:
    return GraphEdge(
        id=identifier,
        source_id=source,
        target_id=target,
        relation_type=relation,
        confidence="verified",
        status="verified",
        quotes=("The payment API writes settled payments to the ledger.",),
    )


NODES = (
    _node("ent-api", "Payment API", "api"),
    _node("ent-ledger", "Ledger", "data_store"),
    _node("ent-service", "Settlement Service", "service"),
    _node("ent-report", "Quarterly Report", "concept"),
)
EDGES = (
    _edge("rel-api-ledger", "ent-api", "ent-ledger"),
    _edge("rel-ledger-service", "ent-ledger", "ent-service"),
    _edge("rel-service-api", "ent-service", "ent-api"),
    _edge("rel-ledger-report", "ent-ledger", "ent-report", "references"),
)


def test_community_colouring_is_stable_across_reloads() -> None:
    first = detect_communities(NODES, EDGES)
    second = detect_communities(tuple(reversed(NODES)), tuple(reversed(EDGES)))

    assert [community.community_id for community in first] == [community.community_id for community in second]
    assert [community.member_ids for community in first] == [community.member_ids for community in second]


def test_every_record_belongs_to_exactly_one_community() -> None:
    communities = detect_communities(NODES, EDGES)

    membership: list[str] = []
    for community in communities:
        membership.extend(community.member_ids)

    assert sorted(membership) == sorted(node.id for node in NODES)
    assert len(membership) == len(set(membership))
    assert all(community.community_id.startswith("community-") for community in communities)


def test_an_isolated_record_appears_as_a_gap() -> None:
    communities = detect_communities(NODES, EDGES)
    gaps = detect_knowledge_gaps(NODES, EDGES, communities)

    isolated = [gap for gap in gaps if gap.gap_type == "isolated-record"]
    assert isolated
    assert isolated[0].node_ids == ("ent-report",)
    assert isolated[0].title == "1 isolated record"

    term, context = research_seed_for_gap(isolated[0], NODES)
    assert term == "Quarterly Report"
    assert context == ""


def test_a_surprising_connection_carries_its_reasons() -> None:
    communities = detect_communities(NODES, EDGES)
    connections = find_surprising_connections(NODES, EDGES, communities)

    assert connections
    assert all(connection.reasons for connection in connections)
    assert all(connection.score >= 3 for connection in connections)
    assert {connection.edge_id for connection in connections} <= {edge.id for edge in EDGES}
    assert "connects data_store to concept" in connections[0].reasons
    assert connections[0].score >= 3


def test_graph_hygiene_reports_stranded_records_dead_ends_and_broken_references() -> None:
    nodes = (
        _node("ent-api", "Payment API", "api"),
        _node("ent-ledger", "Ledger", "data_store"),
        _node("ent-orphan", "Orphan", "concept"),
    )
    edges = (_edge("rel-api-ledger", "ent-api", "ent-ledger"), _edge("rel-broken", "ent-api", "ent-missing"))

    findings = detect_graph_hygiene(nodes, edges)
    rules = {finding.rule_id: finding for finding in findings}

    assert ORPHAN_RULE_ID in rules
    assert rules[ORPHAN_RULE_ID].target_id == "ent-orphan"
    assert DEAD_END_RULE_ID in rules
    assert rules[DEAD_END_RULE_ID].target_id == "ent-ledger"
    assert BROKEN_REFERENCE_RULE_ID in rules
    assert rules[BROKEN_REFERENCE_RULE_ID].target_id == "rel-broken"
    assert "ent-missing" in rules[BROKEN_REFERENCE_RULE_ID].message
