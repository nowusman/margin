# Margin Docker Deployment

This guide describes the containerized deployment of Margin using Docker Compose for local development, staging environments, and enterprise infrastructure.

Margin is a greenfield modular monolith. New deployments initialize an empty database on PostgreSQL 18.4 with `pgvector` and apply the checked-in Alembic migration chain before the API opens for HTTP traffic. There are no legacy data migrations or backward compatibility adapters.

## Table of contents

- [Compose architecture](#compose-architecture)
- [Prerequisites](#prerequisites)
- [Configuration and environment variables](#configuration-and-environment-variables)
- [Running the stack](#running-the-stack)
- [Database migration and domain seeding](#database-migration-and-domain-seeding)
- [Model Context Protocol (MCP) server deployment](#model-context-protocol-mcp-server-deployment)
- [Operational health and readiness verification](#operational-health-and-readiness-verification)
- [Security boundaries and authentication](#security-boundaries-and-authentication)
- [Backup, restore, and disaster recovery](#backup-restore-and-disaster-recovery)
- [Troubleshooting](#troubleshooting)

## Compose architecture

The production-grade deployment stack in `infra/docker-compose.yml` provides:

- **PostgreSQL 18.4**: PostgreSQL 18.4 with `pgvector` 0.8.5 (`pgvector/pgvector:0.8.5-pg18`) for relational state, foreign key integrity, full-text indexes, and high-performance vector search.
- **Redis 7**: Redis 7 Alpine (`redis:7-alpine`) coordinating asynchronous Celery message passing and task result caching.
- **MinIO**: S3-compatible object storage preserving immutable source artifacts and generated previews.
- **API Service**: FastAPI application (`apps/api`) running with ASGI workers and health probe endpoints.
- **Web Service**: React 18 frontend single-page application built with Vite and served via an optimized Nginx container.
- **Unified Celery Worker**: Single container image (`workers/`) consuming tasks across six logical queues (`ingestion`, `extraction`, `embedding`, `review`, `maintenance`, `export`).
- **Initialization Helpers**: One-shot init containers `object-store-init` (creates S3 buckets) and `model-cache-init` (pre-downloads local Sentence Transformers models).
- **Keycloak 26 (Optional)**: OpenID Connect identity provider enabled via the `--profile auth` Compose flag.

### Host and port bindings

| Service | Host URL / Port | Container Port | Purpose |
| --- | --- | --- | --- |
| Web Application | `http://localhost:5173` | 80 | React single-page interface |
| API Service | `http://localhost:8000` | 8000 | FastAPI REST API and `/docs` |
| Keycloak (optional) | `http://localhost:8082` | 8080 | OIDC identity provider |
| PostgreSQL 18.4 | `localhost:15432` | 5432 | Authoritative relational and vector store |
| Redis 7 | `localhost:16379` | 6379 | Celery queue broker |
| MinIO API | `http://localhost:19000` | 9000 | S3-compatible object storage API |
| MinIO Console | `http://localhost:19001` | 9001 | Web management console for S3 buckets |

## Prerequisites

- **Host Operating System**: Windows 10/11 with Docker Desktop (WSL2 backend), Ubuntu 22.04/24.04 LTS, or macOS 13+.
- **Docker**: Docker Engine 24+ and Docker Compose v2.20+.
- **Hardware Resources**: Minimum 8 GB RAM and 4 CPU cores (16 GB RAM recommended when executing local embedding rerankers).
- **Network Ports**: Available local ports 5173, 8000, 15432, 16379, 19000, 19001, and 8082.

## Configuration and environment variables

Configuration is managed via `.env`. Generate the local environment configuration from `.env.example`:

```powershell
Copy-Item .env.example .env
```

### Critical environment parameters

| Parameter | Default in .env.example | Description |
| --- | --- | --- |
| `MARGIN_ENV` | `local` | Environment mode (`local`, `staging`, `production`) |
| `POSTGRES_DB` | `margin` | PostgreSQL database name |
| `POSTGRES_USER` | `margin` | PostgreSQL database user |
| `POSTGRES_PASSWORD` | `margin` | PostgreSQL database password |
| `MARGIN_DATABASE_URL` | `postgresql+psycopg://...` | Connection URL for API and workers |
| `MARGIN_REDIS_URL` | `redis://redis:6379/0` | Redis connection URL |
| `MARGIN_OBJECT_STORE_ENDPOINT` | `http://minio:9000` | Internal S3 endpoint for containers |
| `MARGIN_OBJECT_STORE_BUCKET` | `margin-artifacts` | S3 bucket name for immutable documents |
| `MARGIN_AUTH_HEADER_FALLBACK` | `true` | Dev fallback headers (`MARGIN_AUTH_HEADER_FALLBACK=true`) |
| `MARGIN_EMBEDDING_MODEL_ID` | `BAAI/bge-small-en-v1.5` | Default Sentence Transformers embedding model |
| `MARGIN_RERANKER_MODEL_ID` | `BAAI/bge-reranker-base` | Default Cross-Encoder reranking model |

## Running the stack

### Standard deployment (Local dev / standalone auth)

Start the core stack including PostgreSQL 18.4, Redis, MinIO, API, Worker, and Web:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml up -d --build
```

### Enterprise deployment with Keycloak OIDC

To run Margin with the integrated Keycloak 26 identity provider:

```powershell
docker compose --env-file .env --profile auth -f infra/docker-compose.yml up -d --build
```

To stop all services and preserve data volumes:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml down
```

## Database migration and domain seeding

The API container applies all 23 Alembic migrations up to `0023_ingestion_job_sequence` automatically on startup.

Once the API container is healthy, seed the default architecture domains, controlled entity vocabulary, initial authorization groups, and model catalog entries:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m app.cli seed-domains
```

## Model Context Protocol (MCP) server deployment

The Margin MCP server runs as a standalone Python process communicating over standard I/O with desktop agents (such as Claude Desktop or Antigravity):

```powershell
python -m margin_mcp.main --api-url http://127.0.0.1:8000 --user mcp-reader --groups architect
```

When integrating with Claude Desktop or Antigravity IDE, register the server configuration in your MCP settings file:

```json
{
  "mcpServers": {
    "margin": {
      "command": "python",
      "args": [
        "-m",
        "margin_mcp.main",
        "--api-url",
        "http://127.0.0.1:8000",
        "--user",
        "mcp-operator",
        "--groups",
        "architect"
      ]
    }
  }
}
```

## Operational health and readiness verification

Verify system readiness using PowerShell or curl:

```powershell
# Check basic API liveness
Invoke-RestMethod http://localhost:8000/health

# Check deep database and converter readiness
Invoke-RestMethod http://localhost:8000/ready
```

The `/ready` endpoint verifies:

1. Active PostgreSQL 18.4 database connectivity.
2. S3 bucket existence and object storage access.
3. System conversion toolchains (Docling, MarkItDown, Pillow, diagram parsers).

## Security boundaries and authentication

The Docker stack does not include an OAuth2 proxy. Do not expose this Docker stack directly to the internet without an ingress gateway, TLS termination, and a reverse proxy enforcing authentication.

In enterprise deployments:

- Configure `MARGIN_AUTH_HEADER_FALLBACK=false` to reject unauthenticated header emulation.
- Point `MARGIN_OIDC_ISSUER_URL` and `MARGIN_OIDC_JWKS_URL` to your corporate OpenID Connect provider.
- Enforce network boundary isolation so that PostgreSQL (port 15432), Redis (port 16379), and MinIO (ports 19000/19001) are accessible only from application containers.

## Backup, restore, and disaster recovery

Margin adheres to a strict disposable infrastructure philosophy:

- **Canonical database backup**: PostgreSQL 18.4 database dumps (`pg_dump`) capture every entity, IdeaBlock, revision, relationship, and vector embedding.
- **Source asset backup**: S3 bucket snapshots (`margin-artifacts`) retain immutable source files.
- **Disaster recovery sequence**:
  1. Initialize fresh PostgreSQL 18.4 container and empty database volume.
  2. Run Alembic migrations to head (`alembic upgrade head`).
  3. Restore S3 object store bucket.
  4. Reprocess sources or restore database state from verified dump.

## Troubleshooting

- **Container startup failure**: Check logs using `docker compose -f infra/docker-compose.yml logs -f api`.
- **Database connection rejected**: Verify PostgreSQL container health with `docker compose -f infra/docker-compose.yml ps postgres`.
- **Worker queue stalling**: Inspect Redis connectivity with `docker compose -f infra/docker-compose.yml exec redis redis-cli ping`.
- **Model cache timeout**: Ensure `model-cache-init` completed successfully with `docker compose -f infra/docker-compose.yml logs model-cache-init`.
