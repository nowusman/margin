"""System-wide graph hygiene scanning.

The record integrity lint asks whether a record is sound. This scan asks
whether the graph is navigable: which records are stranded, which lead
nowhere, and which relationships point at records that are no longer active.

The scan runs without a requesting principal, because it is a governance sweep
over the whole corpus rather than a statement to a reader. Its findings become
review work, never a published conclusion.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from margin_db.graph_subgraph import full_scope_subgraph
from margin_graph import detect_graph_hygiene
from margin_schema.findings import HygieneFinding


def run_graph_hygiene_lint(session: Session) -> list[HygieneFinding]:
    """Report stranded records, dead ends and broken references."""

    subgraph = full_scope_subgraph(session)
    return list(detect_graph_hygiene(subgraph.nodes, subgraph.edges))
