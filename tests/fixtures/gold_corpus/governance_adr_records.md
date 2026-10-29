# ADR-0042: Adopt PostgreSQL with pgvector for Vector Similarity Search

Record Type: adr
ID: adr-0042
Owner: Platform Architect
Status: accepted
Domain: platform
Created Date: 2026-03-15
Updated Date: 2026-04-01

## Context

Vector retrieval is required for hybrid semantic search over atomic Idea Blocks without operating a distinct vector database cluster.

## Decision

Adopt PostgreSQL 16 with the pgvector extension and HNSW indexing for all dense embedding storage.

## Consequences

Dedicated vector engines (Qdrant, Pinecone) are eliminated from the critical runtime.
Operational simplicity is preserved within the existing PostgreSQL relational backup and replication topology.

## Audit Events

- 2026-03-15: Created in draft state by user-arch-01
- 2026-04-01: Transitioned from proposed to accepted by user-arch-lead
