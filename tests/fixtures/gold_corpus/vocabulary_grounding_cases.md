# Governed Architecture Vocabulary and Ontology Grounding Cases

## 1. Canonical Terms and Identifiers

- Canonical Entity: PostgreSQL (ID: `ent-postgresql`, kind: `database`)
- Canonical Entity: Kubernetes (ID: `ent-kubernetes`, kind: `orchestration_platform`)
- Canonical Entity: Apache Kafka (ID: `ent-kafka`, kind: `event_broker`)

## 2. Governed Aliases

- "K8s" resolves deterministically to canonical Entity `ent-kubernetes`.
- "PG" and "Postgres" resolve deterministically to canonical Entity `ent-postgresql`.
- "Kafka" resolves deterministically to canonical Entity `ent-kafka`.

## 3. Contextual Homonyms

- Context Database: "Cluster" refers to PostgreSQL High Availability Replica Cluster (`ent-postgresql-cluster`).
- Context Compute: "Cluster" refers to Kubernetes Container Compute Cluster (`ent-kubernetes-cluster`).
- Homonym mentions without unambiguous context must be flagged for human review disambiguation.

## 4. Unknown and Ungrounded Terms

- The unrecognized term "SuperGraphMeshV3" is mentioned in candidate text.
- Ungrounded policy: Margin must record an unresolved mention and generate a review task; it must NEVER create synthetic phantom entities.

## 5. Architectural Hierarchy

- Domain: Platform (`dom-platform`)
- Parent System: Customer Platform (`ent-customer-platform`)
- Child Service: Billing API (`ent-billing-api`)
- Child Component: Invoice Generator (`ent-invoice-gen`)

## 6. Ontology Relation Validation & Invalid Relation Constraints

- Valid Relation: Service `Billing API` -> `depends_on` -> System `Customer Platform` (allowed by ontology).
- Valid Relation: Mitigation `Span Review` -> `mitigates` -> Risk `Stale Docs` (allowed by ontology).
- Invalid Relation Attempt: Service `Billing API` -> `mitigates` -> Service `Order Service`.
- Rule: Attempting to assert `mitigates` between two Services violates ontology constraints and must be rejected at validation time.
