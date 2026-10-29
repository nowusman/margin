# DEBT-0078: Direct Database Queries Bypassing Order Service Domain Boundary

Record Type: technical_debt
ID: debt-0078
Owner: Principal Engineer
Status: identified
Domain: platform
Estimated Remediation Cost: 120 hours
Interest Score: 8.5 / 10

## Description

Reporting batch workers perform direct SQL read queries against the Order Service relational database, breaking encapsulation.

## Remediation Plan

Expose paginated asynchronous query endpoints on Order Service API and migrate downstream workers.

## Linked Entities

- Source Entity: Reporting Worker
- Target Entity: Order Service Database

## Audit Events

- 2026-02-18: Recorded by user-principal-01
- 2026-03-02: Interest score updated to 8.5 due to database migration conflicts
