# BACKLOG-0012: Refactor Ingestion Pipeline to Consolidated Worker Profile

Record Type: architecture_backlog
ID: backlog-0012
Owner: Staff Systems Engineer
Status: pending
Domain: platform
Priority: high
External Issue Reference: PROJ-14029
External Issue URL: <https://tracker.corp.internal/issues/PROJ-14029>

## Interim Arrangement

Currently multiple Celery worker containers run specialized roles, causing memory fragmentation and idle worker overhead.

## Architecture Correction

Consolidate to a single worker profile consuming prioritized queues (ingest, extract, embedding, review, maintenance).

## Correction Lineage

- Arises from Interim Architecture Decision: Split Workers Strategy 2024
- Corrective Target: Single Worker Modular Architecture

## Audit Events

- 2026-04-10: Created by user-staff-01
- 2026-04-12: External issue URL validated and bound
