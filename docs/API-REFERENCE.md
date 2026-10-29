# Margin HTTP API Reference

The Margin HTTP API provides enterprise-grade, role-governed endpoints for knowledge ingestion, claim extraction, canonical entity catalog management, explicit relationship traversal, unified architecture governance, hybrid search, cited question answering ("Ask Margin"), personal workspaces, and autonomous agent orchestration.

All endpoints are mounted under `/api`, with system health and readiness checks exposed at the root. Authentication is enforced via Bearer tokens or verified identity headers, evaluated against a multi-dimensional Role-Based Access Control (RBAC) model.

## Table of contents

- [System and session endpoints](#system-and-session-endpoints)
- [Sources and document ingestion](#sources-and-document-ingestion)
- [Ingestion jobs and tracking](#ingestion-jobs-and-tracking)
- [Extraction and element provenance](#extraction-and-element-provenance)
- [IdeaBlocks and claim evidence](#ideablocks-and-claim-evidence)
- [Canonical entities and controlled vocabulary](#canonical-entities-and-controlled-vocabulary)
- [Relationships and impact traversal](#relationships-and-impact-traversal)
- [Knowledge graph and analytics](#knowledge-graph-and-analytics)
- [Unified architecture governance](#unified-architecture-governance)
- [Governance review queue](#governance-review-queue)
- [Retrieval and "Ask Margin"](#retrieval-and-ask-margin)
- [Domains, charters, and narratives](#domains-charters-and-narratives)
- [Personal workspace ("Your Space")](#personal-workspace-your-space)
- [Autonomous agent runtime](#autonomous-agent-runtime)
- [Deterministic domain export bundles](#deterministic-domain-export-bundles)
- [Dashboards, C4 explorer, and diagrams](#dashboards-c4-explorer-and-diagrams)
- [Quality and evaluation harness](#quality-and-evaluation-harness)
- [Platform administration and user access](#platform-administration-and-user-access)

## System and session endpoints

### `GET /health`

Basic service liveness probe.

- **Roles**: Public / All roles.
- **Response `200 OK`**:

  ```json
  {
    "status": "ok",
    "service": "margin-api"
  }
  ```

### `GET /ready`

Deep system readiness probe validating database connectivity and converter dependencies.

- **Roles**: Public / All roles.
- **Response `200 OK`**:

  ```json
  {
    "status": "ready",
    "environment": "local",
    "system_of_record": "postgresql",
    "object_store_bucket": "margin-sources",
    "release_status": "ready",
    "database": "ready",
    "converter_dependencies": "ready"
  }
  ```

- **Error `503 Service Unavailable`**: Returned if PostgreSQL or document converters are not functional.

### `GET /api/rbac/me` (and `GET /rbac/me`)

Inspects active caller principal identity, groups, and effective domain/category/sensitivity permissions.

- **Roles**: Authenticated principal.
- **Response `200 OK`**:

  ```json
  {
    "principal_id": "architect-1",
    "auth_source": "oidc",
    "group_ids": ["architect", "governance-admin"],
    "domain_permissions": { "payments": ["read", "write", "review"] },
    "category_permissions": { "governance:adr": ["read", "write"] },
    "sensitivity_permissions": { "confidential": ["read", "write"] },
    "authz_backend": "database"
  }
  ```

## Sources and document ingestion

### `POST /api/sources`

Uploads an individual architecture document into the ingestion pipeline.

- **Roles**: `Architect`, `Domain Steward`, `Governance Admin`, `Platform Admin`.
- **Content-Type**: `multipart/form-data`.
- **Form parameters**:
  - `file`: Document binary (PDF, DOCX, PPTX, PNG, JPEG, TIFF, WebP, Markdown, YAML, JSON, Structurizr, Mermaid, PlantUML, OpenAPI, AsyncAPI).
  - `domain_id`: Target architecture domain identifier.
  - `category`: Source document category (e.g., `design`, `adr`, `standard`).
  - `sensitivity`: Data sensitivity (`internal`, `confidential`, `restricted`).
- **Response `201 Created`**: Returns the created `SourceDocument` record and queued `IngestionJob`.

### `POST /api/sources/batch`

Batch upload of up to 50 documents simultaneously while preserving folder hierarchies.

- **Roles**: `Architect`, `Domain Steward`, `Governance Admin`, `Platform Admin`.
- **Content-Type**: `multipart/form-data`.
- **Form parameters**: `files` (array), `relative_paths` (JSON array of strings), `domain_id`.
- **Response `201 Created`**: Batch summary and list of created source IDs and ingestion job IDs.

### `GET /api/sources`

List readable source documents with pagination and metadata filtering.

- **Query parameters**: `domain_id`, `category`, `status`, `limit`, `offset`.
- **Response `200 OK`**: Array of source document cards.

### `GET /api/sources/{id}`

Get full metadata, processing stage, and version details for a specific document.

### `PATCH /api/sources/{id}`

Update mutable source metadata (title, category, sensitivity).

### `DELETE /api/sources/{id}`

Permanently delete source document and its extracted elements and IdeaBlock relationships.

### `GET /api/sources/{id}/pages`

Retrieve converted pages for a source document.

### `GET /api/sources/{id}/elements`

Retrieve converted elements with character offsets, element types, and bounding box coordinates.

### `GET /api/sources/{id}/preview`

Retrieve rendered Markdown or HTML preview of converted source document.

### `GET /api/sources/{id}/file`

Download original, immutable source binary directly from S3 object storage.

### `GET /api/sources/{id}/versions`

List all historical versions of a source document.

### `POST /api/sources/{id}/reprocess`

Trigger re-ingestion and extraction on an existing document, producing a version diff report.

## Ingestion jobs and tracking

- `GET /api/jobs`: List user's active and completed ingestion jobs.
- `GET /api/jobs/{id}`: Detailed job execution status, current stage (`converting`, `extracting`, `resolving`, `embedding`), and stage durations.
- `POST /api/jobs/{id}/retry`: Retry a failed or quarantined ingestion job.
- `POST /api/jobs/{id}/cancel`: Abort an in-flight ingestion job.
- `GET /api/admin/ingestion-jobs`: Administrator view across all system ingestion jobs.
- `POST /api/admin/ingestion-jobs/{id}/retry`: Administrative forced retry.
- `POST /api/admin/ingestion-jobs/{id}/cancel`: Administrative job cancellation.

## Extraction and element provenance

### `POST /api/sources/{id}/extract`

Execute deterministic or model-guided extraction on converted document elements.

### `GET /api/sources/{id}/ingestion-receipt`

Retrieve formal receipt accounting for every source element: converted elements, extracted claims, resolved entities, and unextracted items.

### `GET /api/sources/{id}/reprocessing-diffs`

Inspect structural diffs between extraction runs across document versions.

### `GET /api/sources/{id}/extraction-report`

Retrieve extraction quality report, precision scores, and warning logs.

## IdeaBlocks and claim evidence

### `GET /api/ideablocks`

Query and filter atomic IdeaBlocks.

- **Query parameters**: `domain_id`, `status` (`candidate`, `verified`, `stale`, `rejected`), `category`, `entity_id`, `query`, `limit`, `offset`.
- **Response `200 OK`**: Paginated list of IdeaBlocks with current revision statement and confidence.

### `POST /api/ideablocks`

Author a manual candidate IdeaBlock.

- **Request body**:

  ```json
  {
    "statement": "Payment gateway communicates with fraud service via mutual TLS over gRPC.",
    "domain_id": "payments",
    "category": "design",
    "sensitivity": "confidential",
    "source_element_id": "el_01hx..."
  }
  ```

- **Response `201 Created`**: Created IdeaBlock with deterministic SHA-256 ID (`ib_...`).

### `GET /api/ideablocks/{id}`

Retrieve IdeaBlock details, complete immutable revision history, linked entities, and governing ontology version.

### `PATCH /api/ideablocks/{id}`

Edit IdeaBlock statement. Creates a new immutable `IdeaBlockRevision` requiring a recorded update reason.

### `PATCH /api/ideablocks/{id}/status`

Transition IdeaBlock lifecycle state (`candidate`, `verified`, `rejected`, `stale`).

### `DELETE /api/ideablocks/{id}`

Retire an IdeaBlock from active search while preserving historical audit records.

### `GET /api/evidence/{evidence_id}`

Retrieve exact quoted text passage, bounding coordinates, page number, and source file metadata for an evidence record.

### `GET /api/audit-events`

Paginated audit log of knowledge modifications, status transitions, and user actions.

## Canonical entities and controlled vocabulary

### `GET /api/entities`

Search and filter the canonical entity catalog.

- **Query parameters**: `domain_id`, `kind` (`System`, `Container`, `Component`, `API`, `Database`, `Service`, `Team`), `status`, `query`, `limit`.

### `POST /api/entities`

Register a new canonical entity in the catalog.

### `GET /api/entities/{id}`

Retrieve canonical entity details, active aliases, relationships, ownership, and revision metadata.

### `PATCH /api/entities/{id}`

Update mutable attributes (description, owner, status) with a required justification reason.

### `POST /api/entities/{id}/approve`

Approve a draft candidate entity into the active canonical catalog.

### `POST /api/entities/{id}/aliases`

Register a new alias, abbreviation, or legacy name for an entity.

### `POST /api/entities/{id}/aliases/{alias_id}/deprecate`

Deprecate an alias with an effective retirement reason.

### `POST /api/entities/{id}/merge`

Merge a duplicate entity into a target canonical entity, automatically redirecting relationships and historical mentions.

### `POST /api/entities/{id}/supersede`

Supersede an entity with its modern architectural successor.

### `POST /api/entities/{id}/retire`

Retire an entity from active architecture status.

### `GET /api/entities/catalog/ontology`

Retrieve governing ontology kinds, permitted relationship types, and constraints.

### `GET /api/entities/mentions`

List unresolved or ambiguous entity mentions identified during document extraction.

### `POST /api/entities/mentions/{mention_id}/resolve`

Bind an unresolved mention to an existing canonical entity or promote it to a new draft entity.

## Relationships and impact traversal

### `GET /api/relationships/neighbors/{entity_id}`

Retrieve bounded direct relationship neighborhood around an entity.

- **Query parameters**: `depth` (1-3), `direction` (`both`, `incoming`, `outgoing`), `relation_types`.

### `POST /api/relationships/path`

Compute the shortest bounded relationship path connecting two canonical entities.

### `GET /api/relationships/impact/{entity_id}`

Perform architectural blast radius and impact analysis for proposed modifications or deprecations.

### `GET /api/relationships/governed-entity/{entity_id}`

Retrieve all ADRs, risks, waivers, technical debt, and backlog items linked to an entity.

### `GET /api/relationships/related-idea-block/{idea_block_id}`

Retrieve entities and related IdeaBlocks connected to a specific claim.

### `POST /api/sources/relationships/{relationship_id}/approve`

Formally approve a candidate relationship into the verified graph.

### `POST /api/sources/relationships/{relationship_id}/reject`

Reject an unverified candidate relationship.

## Knowledge graph and analytics

### `GET /api/graph/subgraph`

Query the authorized knowledge graph dynamically for visual exploration.

### `GET /api/graph/communities`

Compute on-demand Louvain community clusters across verified entities and relationships.

### `GET /api/graph/insights`

Detect unexpected cross-domain architecture connections and identified knowledge gaps.

### `GET /api/graph/hygiene`

Evaluate graph integrity: dangling edges, circular dependencies, orphan entities, and dead-end nodes.

### `POST /api/graph/insights/decisions`

Record a reader attention decision (`dismiss` or `defer`) on a surfaced insight or gap finding.

## Unified architecture governance

### `GET /api/governance/records`

Query governance records (ADRs, risks, waivers, technical debt, architecture backlog).

- **Query parameters**: `record_type`, `domain_id`, `status`, `sensitivity`, `limit`, `offset`.

### `POST /api/governance/records`

Create a new governance record.

### `GET /api/governance/records/{id}`

Retrieve detailed governance record including revisions, linked entities, and audit history.

### `PATCH /api/governance/records/{id}`

Update mutable governance record fields.

### `POST /api/governance/records/{id}/transitions`

Execute a lifecycle state transition.

### `POST /api/governance/records/{id}/relations`

Establish cross-governance relationships (e.g., Risk mitigates ADR).

### `POST /api/governance/records/{id}/idea-blocks`

Link an IdeaBlock to a governance record.

### `DELETE /api/governance/records/{id}/idea-blocks/{idea_block_id}`

Unlink an IdeaBlock from a governance record.

### `POST /api/governance/records/{id}/entities`

Link a canonical entity to a governance record.

### `DELETE /api/governance/records/{id}/entities/{entity_id}`

Unlink a canonical entity from a governance record.

### Specialized lifecycle endpoints

- `POST /api/governance/records/{id}/waiver-renewals`: Renew an architecture waiver with an updated expiry.
- `POST /api/governance/records/{id}/waiver-revocations`: Revoke an active waiver.
- `POST /api/governance/records/{id}/risk-acceptances`: Formally accept an identified architecture risk.
- `POST /api/governance/records/{id}/risk-closures`: Close an architectural risk.
- `POST /api/governance/records/{id}/technical-debt-resolutions`: Mark technical debt as resolved.
- `POST /api/governance/records/{id}/architecture-backlog-completions`: Mark backlog initiative as complete.
- `POST /api/governance/records/{id}/supersede`: Supersede governance record with a successor.

### Governance health and contradiction detection

- `GET /api/governance/health/lint-report`: Consistency and coverage report across governance records.
- `POST /api/governance/contradictions/detect`: Scan for conflicting statements across claims.
- `POST /api/governance/blind-spots/detect`: Scan for unmonitored architectural blind spots.

## Governance review queue

- `POST /api/governance/review/tasks`: Create a review task.
- `GET /api/governance/review/tasks`: List pending review tasks with priority and age filtering.
- `GET /api/governance/review/tasks/{id}`: Detailed review task description.
- `GET /api/governance/review/tasks/{id}/bundle`: Retrieve assembled evidence bundle.
- `POST /api/governance/review/tasks/{id}/decision`: Record semantic review decision.
- `POST /api/governance/review/tasks/{id}/action`: Execute approval or rejection action on review bundle.
- `PATCH /api/governance/review/tasks/{id}`: Update task assignment or status.

## Retrieval and "Ask Margin"

### `POST /api/search`

Execute governed hybrid search across IdeaBlocks.

- **Request body**:

  ```json
  {
    "query": "How is customer payment data encrypted at rest?",
    "top_k": 10,
    "filters": {
      "domain_ids": ["payments"],
      "statuses": ["verified"],
      "sensitivities": ["confidential"]
    },
    "expand": { "depth": 1, "limit": 5 }
  }
  ```

- **Response `200 OK`**: Candidate cards, reciprocal rank fusion scores, snippets, and citations.

### `POST /api/ask`

AI-assisted question answering strictly grounded in verified evidence.

- **Request body**: Extends search request with `thread_id`, `output_language`, and `context_budget_chars`.
- **Response `200 OK`**:

  ```json
  {
    "answer": "Payment data is encrypted using AES-256-GCM with customer-managed keys [^1].",
    "answer_citation_ids": ["ev_01hx..."],
    "response_mode": "answer",
    "model": "minimax-m3",
    "results": [...]
  }
  ```

- **Fallback behavior**: Returns `response_mode: "search_only"` if evidence citations cannot be verified.

### `POST /api/search/source-discovery`

Discovers source documents matching terms without ranking individual IdeaBlocks.

### `GET /api/retrieval/filter-options`

Retrieves authorized filter dropdown options (domains, categories, statuses) for current user.

## Domains, charters, and narratives

- `GET /api/domains`: List architectural domains.
- `POST /api/domains`: Create a new domain.
- `DELETE /api/domains/{id}`: Remove a domain.
- `GET /api/domains/{id}/charter`: Read domain charter.
- `PUT /api/domains/{id}/charter`: Author or update domain charter (purpose, scope, terminology).
- `GET /api/domains/{id}/briefing`: Composed executive domain briefing.
- `GET /api/domains/{id}/known-unknowns`: Unresolved architecture entities or missing evidence.
- `GET /api/domains/{id}/narrative`: Dynamic domain narrative distinguishing verified from candidate claims.

## Personal workspace ("Your Space")

- `GET /api/personal/notes`, `PUT /api/personal/notes`: Manage private notes.
- `DELETE /api/personal/notes/{note_id}`: Remove personal note.
- `GET /api/personal/saved`, `PUT /api/personal/saved`: Read and bookmark records.
- `DELETE /api/personal/saved/{item_id}`: Remove bookmark.
- `POST /api/personal/views`: Save custom filtered query view.
- `GET /api/personal/feed`: Personalized activity stream and reading recommendations.
- `GET /api/personal/threads`, `POST /api/personal/threads`: List and create Ask Margin conversation threads.
- `GET /api/personal/threads/{thread_id}`, `DELETE /api/personal/threads/{thread_id}`: Manage multi-turn threads.

## Autonomous agent runtime

- `POST /api/agent/runs`: Start bounded agent execution with goal, token budget, and iteration limit.
- `GET /api/agent/runs`: List historical and active agent runs.
- `GET /api/agent/runs/{id}`: Inspect run progress, token consumption, and event stream.
- `POST /api/agent/runs/{id}/cancel`: Abort active run.
- `POST /api/agent/runs/{id}/proposals`: Submit structured diff proposal (`AgentProposal`).
- `POST /api/agent/proposals/{id}/approve`: Approve proposed changes (routes canonical items to review queue).
- `POST /api/agent/proposals/{id}/reject`: Reject proposal.
- `POST /api/agent/requests/{id}/answer`: Answer structured mid-run human input prompt.
- `GET /api/agent/playbooks`: List discovered domain playbooks.

## Deterministic domain export bundles

- `POST /api/exports`: Enqueue a scope-bounded domain export.
- `GET /api/exports/{id}`: Check generation status and manifest.
- `GET /api/exports/{id}/manifest`: Inspect export manifest, inclusions, exclusions, and SHA-256 hash.
- `GET /api/exports/{id}/download`: Download deterministic `.zip` export bundle.

## Dashboards, C4 explorer, and diagrams

- `GET /api/dashboard/summary`: Overall architecture health metrics, corpus stats, and review pressure.
- `GET /api/dashboard/heatmap`: Domain architecture health heatmap.
- `GET /api/c4/tree`: Hierarchical C4 model (Domain -> System -> Container -> Component).
- `GET /api/c4/overlays`: C4 entity counts and governance links.
- `GET /api/diagrams`: List registered architecture diagrams.
- `GET /api/diagrams/{id}`: Get diagram source.
- `GET /api/diagrams/{id}/render`: Render diagram as SVG or client-side Mermaid format.

## Quality and evaluation harness

- `GET /api/evaluations/runs`: List gold-corpus evaluation runs.
- `POST /api/evaluations/runs`: Trigger an evaluation run against gold corpus fixtures.
- `GET /api/evaluations/runs/{id}`: Detailed evaluation report, precision/recall metrics, and release gates.

## Platform administration and user access

- `GET /api/admin/models`, `POST /api/admin/models`: Manage registered LLM, embedding, and reranker models.
- `PATCH /api/admin/models/{id}`, `DELETE /api/admin/models/{id}`: Update or delete model configurations.
- `POST /api/admin/models/{id}/test`: Execute capability-aware health test on configured model.
- `GET /api/admin/models/health`: Aggregate health status across all configured models.
- `GET /api/admin/models/configuration-checks`: Validate model parameters and secret references.
- `GET /api/admin/system-health`: Real-time connectivity status for DB, Redis, MinIO, and converters.
- `GET /api/admin/audit-events`: System-wide security and modification audit log.
- `GET /api/admin/access`, `POST /api/admin/access`: Manage user RBAC access profiles.
- `PATCH /api/admin/access/{user}`, `DELETE /api/admin/access/{user}`: Update or remove user access profile.
- `POST /api/admin/access/check`: Dry-run test of user permissions against a target resource.
