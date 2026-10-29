# Alert Policies and Operational Escalation

Margin operational alerts use Margin application health data, parent worker job state, PostgreSQL records, audit events, and completed evaluation runs. Alert data must come from retained runtime records; no connector or persisted assistant-trace signal exists.

All production alerts trigger actionable incident workflows to maintain high availability and data integrity across the platform.

## Table of contents

- [Operational metrics and telemetry sources](#operational-metrics-and-telemetry-sources)
- [Alert signals and monitoring thresholds](#alert-signals-and-monitoring-thresholds)
- [Escalation discipline and on-call procedures](#escalation-discipline-and-on-call-procedures)
- [Incident triage and evidence preservation](#incident-triage-and-evidence-preservation)
- [Recovery verification and incident closure](#recovery-verification-and-incident-closure)

## Operational metrics and telemetry sources

Margin monitors runtime operations across four primary sources:

1. **Application health endpoints**: Direct probes from `/health` and `/ready`.
2. **PostgreSQL records**: Transaction states, migration levels, and entity consistency in PostgreSQL 18.4.
3. **Worker job state**: Celery queue lengths, active job progressions, and failed ingestion jobs in Redis 7.
4. **Audit events**: Permanent audit logs recorded for every state-changing governance transition and review action.

## Alert signals and monitoring thresholds

| Signal | Source | Initial Owner | Escalation Condition |
| --- | --- | --- | --- |
| API or dependency readiness failure | `/health`, `/ready`, container health | Platform operator | Readiness failure persists beyond 30 seconds |
| Failed ingestion jobs | `/api/jobs`, audit events, worker logs | Ingestion operator | Error rate exceeds 2% or retries are exhausted |
| Oldest pending review task age | `/api/governance/review/tasks` | Governance steward | Oldest pending task age exceeds agreed SLA (48h) |
| Retrieval latency p95 | Telemetry and evaluation runs | Retrieval operator | Retrieval latency p95 exceeds 450ms for 5m |
| Citation regression | Evaluation runs and search telemetry | Retrieval operator | Citation sufficiency falls below 98% threshold |
| Average answer faithfulness score | Grounded-answer evaluation runs | Retrieval operator | Average answer faithfulness score drops below 0.90 |
| Relationship consistency failure | Maintenance results and review tasks | Governance steward | Unresolved error-severity relation findings |
| Graph hygiene findings | Maintenance tasks (`margin.maintenance.health_check`) | Governance steward | Dangling edges or broken references unaddressed |
| Known-unknown accumulation | Maintenance tasks and unresolved mentions | Domain steward | Unresolved mentions backlog exceeds domain threshold |
| Export failure or stall | Export bundle queue and worker logs | Platform operator | Export job stays in composing state over 10m |
| Agent run budget exhaustion | Agent run records and run events | Platform operator | Aborted agent runs exceed 5% of hourly volume |
| Agent proposal backlog age | Agent proposals in review queue | Domain steward | Undecided proposals older than 72 hours |
| Model endpoint failure | Admin model health probes (`/api/admin/models/health`) | Platform operator | Configured active model endpoint health check fails |

## Escalation discipline and on-call procedures

- **Tier 1 (Automated alert)**: System detects metric breach and pages on-call engineer via alerting policy.
- **Tier 2 (Operator triage)**: Engineer investigates failing parent job, container logs, or database query telemetry.
- **Tier 3 (Domain / Governance steward)**: Content-level anomalies (conflicting claims, expired waivers, entity merges) are routed to domain stewards via review tasks (`/api/governance/review/tasks`).

## Incident triage and evidence preservation

For any production incident:

1. **Preserve evidence**: Do not blindly restart containers. Capture failing run events, parent job receipts, and Celery error traces first.
2. **Identify blast radius**: Determine whether the failure affects document conversion, claim extraction, search retrieval, or export generation.
3. **Record audit trail**: All manual interventions, job cancellations, and retries must be recorded with an operator reason.

## Recovery verification and incident closure

A restart or a single healthy probe does not prove recovery:

- Verify sustained `/ready` status for at least 5 consecutive probe intervals.
- Execute a sample end-to-end ingestion or search request to confirm functional data flow.
- Ensure pending review tasks and stalled worker queues return to normal operating boundaries before closing the incident ticket.
