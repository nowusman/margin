# Agent, Export, and Graph Operations Runbook

This runbook covers operational procedures for the background execution surfaces in Margin: knowledge graph scans, reader decisions, ontology re-evaluations, domain exports, autonomous agent runs, playbooks, and the Model Context Protocol (MCP) server.

These execution surfaces operate over the canonical PostgreSQL 18.4 record layer.

## Table of contents

- [Graph hygiene and anomaly scans](#graph-hygiene-and-anomaly-scans)
- [Reader decisions on graph findings](#reader-decisions-on-graph-findings)
- [Ontology re-evaluation procedures](#ontology-re-evaluation-procedures)
- [Known-unknowns resolution workflow](#known-unknowns-resolution-workflow)
- [Domain export operations and monitoring](#domain-export-operations-and-monitoring)
- [Autonomous agent runtime operations](#autonomous-agent-runtime-operations)
- [Model Context Protocol (MCP) server operations](#model-context-protocol-mcp-server-operations)
- [Playbook discovery and authoring](#playbook-discovery-and-authoring)
- [Operational troubleshooting](#operational-troubleshooting)

## Graph hygiene and anomaly scans

The graph hygiene scan runs periodically on the `maintenance` queue via the `margin.maintenance.health_check` Celery task. It evaluates the entire corpus for structural anomalies:

| Anomaly Rule | Description | Default Severity | Review Action |
| --- | --- | --- | --- |
| `graph_orphan_record` | Entity has no relationships to other entities | Info | Review whether entity is unowned or unlinked |
| `graph_dead_end_record` | Entity has incoming edges but zero outgoing edges | Info | Verify if downstream dependencies were omitted |
| `graph_broken_reference` | Relationship points to retired or superseded entity | Warning | Update relationship target or retire relation |

Findings are automatically de-duplicated and routed to the governance review queue (`/api/governance/review/tasks`).

## Reader decisions on graph findings

When exploring graph insights or knowledge gaps, readers can record attention decisions via:

```text
POST /api/graph/insights/decisions
{
  "target_id": "gap_01hx...",
  "target_type": "gap",
  "decision": "defer"
}
```

- **`dismiss`**: The finding is permanently suppressed for that reader.
- **`defer`**: The finding is temporarily hidden for a configurable number of days, returning when the deferral period expires.
- **Research tasks**: A knowledge gap can be converted into a formal research task via the review task API without modifying canonical data.

## Ontology re-evaluation procedures

When the governing ontology evolves (`margin_schema.ontology.ONTOLOGY_VERSION`), the background worker automatically checks records written under superseded ontology versions:

1. Records supported by the updated ontology are restamped with the new version hash.
2. Records violating the updated rules are marked as withdrawn and routed to review tasks.
3. System health metrics (`ontology_records_reevaluated` and `ontology_records_withdrawn`) report the re-evaluation counts.

## Known-unknowns resolution workflow

A name referenced in architecture documents but lacking a canonical catalog record is classified as a "known unknown":

- The maintenance scan groups unresolved mentions by normalized name.
- Generates an acquisition task in the review queue per group.
- Stewards clear the gap by either:
  1. Creating a canonical entity for the concept.
  2. Binding the mention to an existing canonical entity.
  3. Recording why the reference is out of scope.

## Domain export operations and monitoring

Domain exports are enqueued from Your Space and processed on the `export` queue (`margin.export.domain`):

- **Queue monitor**: Check active export tasks using Celery inspection commands.
- **Permission scoping**: The worker validates requester permissions at generation time.
- **Determinism check**: Verify that repeated exports over unchanged domains produce identical SHA-256 hashes.
- **Storage clean-up**: Completed export `.zip` bundles are stored in the object store with private read policies.

## Autonomous agent runtime operations

The agent runtime executes bounded research runs:

- **Budget enforcement**: Runs must declare explicit `max_iterations` (default: 10) and `token_budget` (default: 8,000).
- **Cancellation**: Operators can terminate runaway runs immediately using `POST /api/agent/runs/{id}/cancel`.
- **Proposal review**: Agent proposals (`AgentProposal`) are inspected via the web console. Approving a canonical change creates a review task rather than writing directly to production.

## Model Context Protocol (MCP) server operations

The MCP server connects IDEs and external AI agents:

- **Health monitoring**: Verify MCP process uptime and stdio pipe availability.
- **API connectivity**: Ensure the MCP server can reach the API at `MARGIN_MCP_API_URL` (default: `http://127.0.0.1:8000`).
- **Audit tracking**: MCP tool invocations are logged with the caller principal in the global audit trail.

## Playbook discovery and authoring

Playbooks provide domain-specific workflow instructions for agents. They are discovered in order of precedence:

1. Operator-supplied custom directories (`MARGIN_PLAYBOOK_DIRS`).
2. Repository-level `./playbooks/` folder.
3. User directory `~/.margin/playbooks/`.

Playbook manifests (`playbook.yaml`) must declare supported domains, triggers, and execution guidelines.

## Operational troubleshooting

- **Stalled worker queues**: Inspect Celery worker status with `celery -A workers.celery_app inspect active`.
- **Failed export generation**: Check worker logs for permission boundary violations.
- **Agent budget exceeded**: Increase token or iteration limits on run creation if task legitimately requires deep multi-step exploration.
