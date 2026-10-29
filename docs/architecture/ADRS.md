# Margin Architecture Decision Records (ADRs)

This document is the consolidated Architecture Decision Log for Margin. It records all accepted, historical, and superseded architectural decision records in chronological sequence.

## Table of contents

- [ADR-0001: PostgreSQL authoritative system of record](#adr-0001-postgresql-authoritative-system-of-record)
- [ADR-0002: Governed inference policy](#adr-0002-governed-inference-policy)
- [ADR-0003: Retrieval strategy (superseded)](#adr-0003-retrieval-strategy-superseded)
- [ADR-0004: Git mirror strategy (superseded)](#adr-0004-git-mirror-strategy-superseded)
- [ADR-0005: Source reuse and clean baseline strategy](#adr-0005-source-reuse-and-clean-baseline-strategy)
- [ADR-0006: Final simplified modular monolith architecture](#adr-0006-final-simplified-modular-monolith-architecture)

## ADR-0001: PostgreSQL authoritative system of record

- **Status**: Accepted
- **Date**: 2026-06-15

### Context

Margin must preserve source document provenance, review state, retrieval traces, architecture entities, explicit relationships, and vector embeddings in a unified operational store with transactional consistency and ACID guarantees.

### Decision

PostgreSQL 18.4 with `pgvector` 0.8.5 is the sole authoritative operational system of record. Git is not authoritative for runtime architecture knowledge. S3 object storage retains immutable raw uploaded artifacts.

### Consequences

- All relational entities, graph structures, vector embeddings (`pgvector`), and audit logs reside in PostgreSQL 18.4.
- Linear Alembic migrations are required for schema evolutions.
- Database rebuilds are reproducible from checked-in migrations.
- External graph and vector databases are rejected in favor of PostgreSQL 18.4 native capabilities.

## ADR-0002: Governed inference policy

- **Status**: Accepted
- **Date**: 2026-07-01

### Context

Margin requires governed inference for claim extraction, entity recognition, and question answering ("Ask Margin"), while ensuring that ungrounded model outputs never pollute verified canonical architecture facts.

### Decision

Inference integration uses configured OpenAI-compatible endpoints or local model runners. Endpoint URLs, model identifiers, context limits, and secret references are administered via the `/api/admin/models` catalog, rather than relying on unmanaged environment variables. Proprietary provider SDKs are excluded from the core runtime.

### Consequences

- Provider SDKs (e.g., Anthropic, proprietary OpenAI libraries) are blocked by static security guards.
- Inference outputs are treated strictly as candidate extractions or candidate narratives, never verified facts.
- Unverifiable or ambiguous outputs route to the human review queue.

## ADR-0003: Retrieval strategy (superseded)

- **Status**: Superseded by [ADR-0006](#adr-0006-final-simplified-modular-monolith-architecture)
- **Date**: 2026-07-20

### Context

Margin initially experimented with external vector databases and complex distributed graph expansion runtimes.

### Decision

This strategy was superseded. PostgreSQL 18.4 with `pgvector` 0.8.5 was chosen to provide hybrid lexical (BM25) and dense vector search in a single transactionally consistent engine using Reciprocal Rank Fusion (RRF).

## ADR-0004: Git mirror strategy (superseded)

- **Status**: Superseded by [ADR-0006](#adr-0006-final-simplified-modular-monolith-architecture)
- **Date**: 2026-08-05

### Context

Early prototypes considered maintaining a bidirectional Git mirror with compiled static markdown files.

### Decision

This decision was retired. Maintaining Git mirrors introduced synchronization lag, conflict drift, and dual systems of record. PostgreSQL 18.4 and S3 object storage remain the sole authoritative stores. The Architecture Wiki is composed dynamically on demand.

## ADR-0005: Source reuse and clean baseline strategy

- **Status**: Accepted
- **Date**: 2026-08-18

### Context

Reference implementations demonstrated atomic claim extraction concepts but included proprietary dependencies, file-first storage models, and tightly coupled SDKs.

### Decision

Margin is engineered as a clean, independent monorepo. Reference repositories serve as conceptual inspirations only; all application packages, data schemas, converters, and algorithms are natively authored and cleanly maintained.

### Consequences

- No external baseline repositories or forks are used.
- Disallowed proprietary dependencies are forbidden from the critical path.
- All reused algorithmic concepts carry full attribution and test coverage.

## ADR-0006: Final simplified modular monolith architecture

- **Status**: Accepted (Supersedes ADR-0003 and ADR-0004)
- **Date**: 2026-09-11

### Context

Margin accumulated prototype complexity including compiled publication storage, Git mirrors, external connector surfaces, and role-specific worker deployments. These duplicated canonical records and added dormant operational overhead.

### Decision

Margin is organized as a unified modular monolith:

1. **Authoritative persistence**: PostgreSQL 18.4 stores canonical sources, IdeaBlocks, entities, relationships, governance records, review tasks, vector embeddings, and audit trails.
2. **Immutable artifacts**: S3-compatible object storage retains immutable source documents and converted previews.
3. **Dynamic surfaces**: The web application renders views, including the Architecture Wiki and C4 Explorer, on demand from canonical API records with zero static markdown builds.
4. **Single worker image**: One unified Celery worker image consumes six queues (`ingestion`, `extraction`, `embedding`, `review`, `maintenance`, `export`).
5. **Governed AI & MCP**: Ask Margin synthesizes answers strictly grounded in authorized citations. An MCP server process allows external IDEs to query the knowledge base over stdio while inheriting full RBAC and audit controls.

### Consequences

- Zero compiled articles, Git vaults, static-site exporters, external database connectors, or persisted retrieval traces remain in the runtime.
- High developer velocity, predictable deployment, zero synchronization drift, and complete audit accountability.
