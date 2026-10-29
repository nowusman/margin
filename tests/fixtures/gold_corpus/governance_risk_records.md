# RISK-0105: Stale Architecture Documentation Causing Implementation Drift

Record Type: risk
ID: risk-0105
Owner: Architecture Steward
Status: open
Severity: high
Domain: platform
Created Date: 2026-02-10

## Statement

Architecture documents can drift from deployed microservices unless automated freshness checks and quarterly span reviews are enforced.

## Treatment Strategy

Mitigate via periodic ingestion re-verification jobs and mandatory review triggers on schema change.

## Linked Mitigations

- Mitigation M-01: Quarterly source element re-verification
- Linked Entity: Customer Platform

## Audit Events

- 2026-02-10: Created by user-steward-01
- 2026-03-01: Assessed as high severity by user-gov-admin
