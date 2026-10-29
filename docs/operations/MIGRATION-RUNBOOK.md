# Margin Database Migration Runbook

- **Scope**: Greenfield target-schema delivery and release cutover
- **Database**: PostgreSQL 18.4 with `pgvector` 0.8.5
- **Policy**: Forward-only Alembic migrations on a disposable database. No legacy parity adapters, compatibility wrappers, dual-read, dual-write, or old-release rollback procedures are supported.

## Table of contents

- [Migration architecture and sequence](#migration-architecture-and-sequence)
- [Target-schema assertions](#target-schema-assertions)
- [Schema rehearsal sequence](#schema-rehearsal-sequence)
- [Production cutover sequence](#production-cutover-sequence)
- [Ownership and estimated duration](#ownership-and-estimated-duration)
- [Disaster recovery and database rebuild](#disaster-recovery-and-database-rebuild)

## Migration architecture and sequence

Margin manages database schema evolutions exclusively through Alembic. The checked-in migration chain consists of exactly 23 linear migrations:

1. `0001_initial_schema`: Core tables, users, groups, and permissions.
2. `0002_canonical_source_provenance`: Source documents, versions, pages, and elements.
3. `0003_idea_block_history`: IdeaBlocks and immutable revision chains.
4. `0004_model_catalog_capabilities`: Model registry and capability profiles.
5. `0005_embedding_targets`: Vector embeddings and search target tables.
6. `0006_semantic_keys`: Semantic keys and deterministic identification.
7. `0007_canonical_entities`: Canonical entity catalog, aliases, and mentions.
8. `0008_unified_governance`: Unified ADRs, risks, waivers, technical debt, and backlog.
9. `0009_search_indexes`: Full-text search (`tsvector`) and `pgvector` cosine indexes.
10. `0010_remove_graph_projections`: Removed legacy graph projection tables.
11. `0011_remove_compiled_git`: Removed legacy Git mirror and compiled markdown tables.
12. `0012_remove_adapters`: Removed dormant external connector persistence.
13. `0013_unified_job_lifecycle`: Consolidated `IngestionJob` lifecycle states.
14. `0014_graph_insight_dismissals`: Reader attention dismissal tracking.
15. `0015_domain_charters`: Versioned domain charters.
16. `0016_personal_layer`: User-scoped notes, bookmarks, custom views, and threads.
17. `0017_export_bundles`: Domain export jobs and manifests.
18. `0018_agent_runtime`: Agent runs, events, proposals, and human inputs.
19. `0019_graph_dismissal_target`: Enhanced dismissal target criteria.
20. `0020_graph_insight_decisions`: Consolidated reader decisions (`dismiss` and `defer`).
21. `0021_domain_context_budget`: Domain-level context character budgets.
22. `0022_resolution_decision_sequence`: Resolution sequence tracking.
23. `0023_ingestion_job_sequence`: High-throughput ingestion job sequence generation.

## Target-schema assertions

The database schema must satisfy the following invariant assertions:

- `alembic_version` is `0023_ingestion_job_sequence`.
- All 23 checked-in migrations apply cleanly in a single execution without requiring application services to run between migrations.
- Legacy tables (`source_sections`, `source_spans`, `knowledge_episodes`, graph projections, Git mirrors) do not exist.
- Parent `idea_blocks` contains no duplicated semantic columns; all statements reside in `idea_block_revisions`.
- `source_pages` contains no mutable processing-state columns.
- Every `idea_blocks.current_revision_id` references an existing `idea_block_revisions.id`.
- Every `claim_evidence.idea_block_revision_id` references an existing revision.
- Every `entity_relationships` row validates permitted source and target entity types.
- Required lexical GIN indexes (`gin(to_tsvector(...))`) and vector indexes exist.

## Schema rehearsal sequence

To rehearse migrations against an isolated PostgreSQL 18.4 container:

```powershell
# 1. Start isolated PostgreSQL 18.4 service
docker compose -f infra/docker-compose.yml up -d postgres

# 2. Create rehearsal database
docker compose -f infra/docker-compose.yml exec postgres psql -U margin -d margin -c "CREATE DATABASE margin_rehearsal;"

# 3. Apply Alembic migration chain
$env:MARGIN_DATABASE_URL = "postgresql+psycopg://margin:margin@127.0.0.1:15432/margin_rehearsal"
python -m alembic -c packages/margin_db/alembic.ini upgrade head

# 4. Verify Alembic head
python -m alembic -c packages/margin_db/alembic.ini current
```

## Production cutover sequence

For clean cutover in deployment environments:

1. **Stop active services**: Temporarily scale down API and worker containers.
2. **Execute migrations**:

   ```powershell
   docker compose -f infra/docker-compose.yml run --rm api python -m alembic -c packages/margin_db/alembic.ini upgrade head
   ```

3. **Execute schema assertions**: Verify Alembic version head and index health.
4. **Deploy matched containers**: Start API, Celery worker, and Web containers together.
5. **Verify readiness**: Confirm `GET /ready` answers `200 OK` with database status `ready`.
6. **Seed baseline data**: Execute `python -m app.cli seed-domains` if initializing greenfield deployment.

## Ownership and estimated duration

| Activity | Responsible Role | Expected Duration |
| --- | --- | --- |
| Stop API and workers | Release engineer | 1 minute |
| Execute Alembic migration chain | Database administrator | 2 minutes |
| Execute target-schema assertions | Database administrator | 1 minute |
| Start API, worker, and web services | Platform engineer | 2 minutes |
| Execute health and readiness checks | Application owner | 2 minutes |
| Total maintenance window | Operations team | < 10 minutes |

## Disaster recovery and database rebuild

Recovery for Margin is a clean rebuild from checked-in migrations followed by reloading source material:

- Preserve all raw source files in S3 object storage.
- Recreate PostgreSQL 18.4 volume.
- Execute Alembic upgrade to head.
- Run `seed-domains` CLI.
- Trigger document reprocessing via `/api/sources/{id}/reprocess`.
