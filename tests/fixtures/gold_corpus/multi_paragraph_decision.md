# Architecture Decision Record 0002: Use Event Sourcing for Billing

## Status

Accepted

## Context

The Billing capability currently mutates the ledger in place.
Auditability of every change is required by the Revenue Engineering team.
Replay of historical billing events is required to reconcile disputes.

## Decision

We will adopt event sourcing for the Billing capability.
The Billing API will append immutable CustomerBilled events to the ledger.

## Consequences

Reconciliation tooling must be built before the next billing cycle.
Database storage costs will increase as event count grows.

## Supersedes

ADR-0001
