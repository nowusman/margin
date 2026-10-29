# Delivered Target Contracts and Knowledge Completeness

This document defines the formal data contracts, API contracts, runtime execution contracts, and knowledge completeness guarantees delivered in Margin.

Margin enforces strict end-to-end accountability from raw source document upload through canonical knowledge records, verified IdeaBlocks, and audited governance decisions.

## Table of contents

- [Data contracts](#data-contracts)
- [API contracts](#api-contracts)
- [Runtime execution contracts](#runtime-execution-contracts)
- [Knowledge completeness guarantees](#knowledge-completeness-guarantees)
- [Source element accounting and receipts](#source-element-accounting-and-receipts)
- [Verification boundaries](#verification-boundaries)
- [Explicit architectural exclusions](#explicit-architectural-exclusions)
- [Release verification and quality gates](#release-verification-and-quality-gates)

## Data contracts

- **Sources & versions**: An ingested source produces immutable versions, normalized pages, and extractable elements. S3 object storage retains immutable raw artifacts; PostgreSQL 18.4 holds canonical metadata and element coordinates.
- **IdeaBlock revisions**: An IdeaBlock revision is atomic, verified, and grounded in citable source elements. Search ranks each current IdeaBlock once, then hydrates all permitted citations.
- **Canonical entities & vocabulary**: `Entity` and `EntityAlias` represent governed architectural concepts. Inferred or ambiguous mentions enter a dedicated review queue.
- **Unified governance records**: `GovernanceRecord` manages ADR, risk, waiver, technical debt, and backlog items. Revisions, typed links, and audit logs are retained permanently.
- **Model catalog**: Model configuration records declare capabilities, context lengths, licensing profiles, and secret references. Secrets are never exposed over health or model query APIs.
- **Explicit relationships**: Relationships carry active ontology contracts, source/target endpoints, confidence scores, review states, temporal validity windows, and source provenance.
- **Single processing lifecycle**: `IngestionJob` is the sole user-visible processing lifecycle. Extraction runs and embedding jobs are linked internal children.

## API contracts

- `/api/sources`: Ingests supported local files and exposes parent-job progress, reprocess diffs, retry, and cancellation.
- `/api/search` and `/api/ask`: Accept governed multi-dimensional filters and return only authorized IdeaBlocks, hydrated citations, and answer synthesis envelopes.
- `/api/governance`: Manages the five governance record types, evidence links, cross-relations, revisions, review transitions, and audit events.
- `/api/relationships`: Exposes bounded, authorized neighbor, shortest path, blast-radius impact, governed-entity, and related-IdeaBlock operations.
- `/api/admin`: Restricts administrative operations (RBAC access profiles, model catalog configuration, system health probes) to platform administrators.

## Runtime execution contracts

- **Modular monolith topology**: Deployment consists of one FastAPI API service, one React/Vite web application, and one unified Celery worker image.
- **Queue partitioning**: The worker consumes six logical queues (`ingestion`, `extraction`, `embedding`, `review`, `maintenance`, `export`).
- **Unified system of record**: PostgreSQL 18.4 with `pgvector` 0.8.5 is the authoritative store for relational data, graph edges, and vector embeddings.
- **Environment template**: `.env.example` is the only supported configuration template.
- **Frontend integration**: The web application communicates exclusively through the authorized HTTP API.

## Knowledge completeness guarantees

Margin accounts for every extractable source element from conversion through canonical knowledge or an explicit terminal outcome:

- **Source element accounting**: Every source version produces citable source elements.
- **Terminal state requirement**: Every processed element is represented by extracted evidence, an audited no-claim outcome, a terminal failure, or quarantine.
- **Verified claim evidence**: Every verified IdeaBlock revision and verified relationship is grounded in active, accessible source element evidence.
- **Review isolation**: Inferred, ambiguous, contradictory, or ungrounded outputs remain review tasks rather than automatically becoming verified knowledge.
- **Version-scoped reprocessing**: Reprocessing is strictly scoped to the source version and does not duplicate existing canonical records.

## Source element accounting and receipts

- **Ingestion receipts**: Report conversion status, element accounting, extraction results, review tasks, and parent-job states separately.
- **Surface quality benchmarks**: Traversal correctness, narrative faithfulness, personal layer scoping, and export completeness are verified against the canonical gold corpus.
- **Charter & playbook boundaries**: Domain charters and playbooks provide extraction guidance. Neither is evidence, and neither can make an ungrounded claim publishable.

## Verification boundaries

- A claim is not publishable merely because it resembles another verified claim.
- A relationship is not queryable as verified without explicit source element provenance.
- A source element outside the reader's authorization scope is never exposed as retrieval context or inline citation text.
- A personal record is not part of the governed corpus and cannot become a published claim.
- An agent proposal is not an applied change; only a human approval applies it, and even then, canonical facts route to review tasks rather than directly into production.

## Explicit architectural exclusions

- **No compiled static wikis or Git mirrors**: Content is dynamically composed from canonical database records.
- **No external vector or graph databases**: No Neo4j, Qdrant, Milvus, Weaviate, or OpenSearch clusters.
- **No ungrounded AI answers**: Answers without verifiable evidence citations are prohibited.
- **No autonomous unreviewed writes**: Agents cannot alter the canonical knowledge base without human review.

## Release verification and quality gates

Production release promotion requires passing all verification gates:

1. **Migration gate**: 23 forward-only Alembic migrations apply cleanly from empty database.
2. **Static guard gate**: `scripts/check_runtime_guards.py` verifies zero blocked provider SDK imports.
3. **Automated test suite**: Python pytest and frontend Vitest suites pass with 100% success.
4. **End-to-end browser suite**: Playwright tests validate live UI workflows across reading, governance, graph, and personal layers.
5. **Surface evaluation gate**: Evaluates graph, narrative, personal, and export surfaces against gold-corpus expectations (`margin-evaluate surfaces`).
