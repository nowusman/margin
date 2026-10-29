# Margin Ontology

## Table of contents

- [Authority and scope](#authority-and-scope)
- [Canonical entity model](#canonical-entity-model)
- [Entity kinds](#entity-kinds)
- [Relationship contract](#relationship-contract)
- [Ontology versions and re-evaluation](#ontology-versions-and-re-evaluation)
- [Reconciliation and extraction rules](#reconciliation-and-extraction-rules)
- [Source terminology and curation](#source-terminology-and-curation)
- [Deliberately excluded legacy behavior](#deliberately-excluded-legacy-behavior)

## Authority and scope

`margin_schema.ontology` is the single machine-readable runtime ontology. Its deterministic `ONTOLOGY_VERSION` hash identifies the active contract, and the runtime requires exactly one active version. The `/api/entities/catalog/ontology` endpoint exposes that exact version and entity-kind registry for catalog controls; relationship consumers use the same runtime registry directly.

This document is the human-readable companion to that contract. It consolidates the relevant terminology and reconciliation guidance from the prior curated source material. Documentation is never a runtime input, and none of the reference material creates a fact or canonical Entity by itself.

Source documents are the only evidence that an instance, property, or relationship exists. The catalog and source terminology may classify, normalize, and validate a source-grounded observation; they never fill in missing architecture from general knowledge.

## Canonical entity model

An `Entity` is the catalog record for a real architectural, organizational, technology, vocabulary, or taxonomy concept. It has one approved ontology type, stable identity, lifecycle, definition, domain, and locale scope. `EntityAlias` holds scoped aliases with lifecycle and provenance. `EntityMention` holds an unresolved or ambiguous source mention. A dictionary entry is a governed candidate, not an automatic Entity or alias.

The following are records, not Entity kinds: source documents, SourceElements, IdeaBlocks and revisions, ADRs, risks, waivers, technical-debt items, and architecture-backlog items. Multi-party source facts remain atomic IdeaBlocks with participant Entity links; Margin does not model hyperedges.

## Entity kinds

| Kind                  | Accepted type aliases                            | Intended use                                                                                |
| --------------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| `api`                 | `interface`, `endpoint`, `application_interface` | Exposed application or technical interface.                                                 |
| `application`         | `app`                                            | Deployed application supporting a business function.                                        |
| `business_capability` | `capability`                                     | Stable organizational ability.                                                              |
| `category`            | —                                                | Controlled source or knowledge classification.                                              |
| `component`           | `module`, `application_component`                | Replaceable architectural component.                                                        |
| `concept`             | —                                                | Named abstract architecture, business, or taxonomy concept; never an unknown-type fallback. |
| `container`           | —                                                | Deployable runtime boundary.                                                                |
| `control`             | `security_control`                               | Security or architecture control.                                                           |
| `data`                | `information`, `record`, `model`                 | Named data, information, record, or model.                                                  |
| `datastore`           | `database`, `data_store`                         | Data persistence system.                                                                    |
| `device`              | —                                                | Physical or virtual computing or network device.                                            |
| `domain`              | `business_domain`, `operational_domain`          | Business or operational management domain.                                                  |
| `event`               | `message`, `notification`                        | Named business or technical event.                                                          |
| `function`            | —                                                | Named behavior that transforms inputs to outputs.                                           |
| `idea_block_type`     | —                                                | Controlled IdeaBlock classification term.                                                   |
| `license`             | —                                                | License, entitlement, agreement, or license pool.                                           |
| `network`             | —                                                | Communications network or network segment.                                                  |
| `node`                | —                                                | Compute, physical, or network node.                                                         |
| `organization`        | —                                                | Organization or enterprise.                                                                 |
| `person`              | —                                                | Individual person.                                                                          |
| `platform`            | —                                                | Application, cloud, or technology platform.                                                 |
| `policy`              | —                                                | Policy or enduring governance rule.                                                         |
| `process`             | —                                                | Named business, operational, or lifecycle process.                                          |
| `repository`          | `architecture_repository`                        | Repository or managed catalog.                                                              |
| `requirement`         | —                                                | Architecture, business, or technical requirement.                                           |
| `role`                | —                                                | Accountable or operational role.                                                            |
| `schema`              | `data_schema`                                    | Data or interface schema.                                                                   |
| `security_scheme`     | `authentication_scheme`                          | Authentication or security scheme.                                                          |
| `service`             | `application_service`, `technology_service`      | Business, application, or technology service.                                               |
| `standard`            | `architecture_standard`                          | Standard, framework, or reference standard.                                                 |
| `system`              | `software_system`                                | Software system or platform-level system.                                                   |
| `tag`                 | —                                                | Controlled vocabulary tag.                                                                  |
| `team`                | `group`, `organizational_unit`                   | Team, group, or organizational unit.                                                        |
| `technology`          | `tool`, `framework`                              | Technology, product, framework, protocol, or tool.                                          |
| `technology_asset`    | `asset`, `it_asset`, `configuration_item`        | Managed technology asset.                                                                   |
| `value_stream`        | —                                                | End-to-end value-creating activity sequence.                                                |

The Architecture Glossary maps Business Capability to `business_capability`, Value Stream to `value_stream`, Architecture Repository to `repository`, and Architecture Assurance and Rationalization & Consolidation to `process`. Architecture Principles, Reference Architecture, Architecture Blueprint, and Application Portfolio are named `concept` or `standard` records according to their source-grounded role. The technology-assets glossary maps Technology Asset to `technology_asset`; owner and custodian to `role`, `person`, `team`, or `organization`; lifecycle management, tracking, and tagging to `process`; and license pools, entitlements, and EULAs to `license`.

## Relationship contract

Every relationship has one persistence class and validates both endpoint types. `managed` below means every Entity kind except `category`, `tag`, and `idea_block_type`; `architecture` means system, application, platform, container, component, service, api, datastore, repository, technology, technology_asset, device, network, node, or security_scheme; and `actor` means person, team, organization, or role.

| Relationship           | Persistence              | Allowed source → target                                                   | Notes                                                        |
| ---------------------- | ------------------------ | ------------------------------------------------------------------------- | ------------------------------------------------------------ |
| `contains`             | EntityRelationship       | any Entity → any Entity                                                   | Inverse: `part_of`.                                          |
| `part_of`              | EntityRelationship       | any Entity → any Entity                                                   | Alias: `is_part_of`; inverse: `contains`.                    |
| `supports`             | EntityRelationship       | managed → managed                                                         | Supporting concept to supported concept.                     |
| `uses`                 | EntityRelationship       | architecture → architecture                                               | User to used concept.                                        |
| `depends_on`           | EntityRelationship       | architecture → architecture                                               | Alias: `depends`; explicit dependency.                       |
| `produces`             | EntityRelationship       | architecture → event                                                      | Producing architecture to emitted event.                     |
| `consumes`             | EntityRelationship       | architecture → event                                                      | Consuming architecture to consumed event.                    |
| `exposes`              | EntityRelationship       | architecture → api, service, event, or schema                             | Aliases: `provides`, `publishes`.                            |
| `secured_by`           | EntityRelationship       | architecture → security_scheme                                            | Explicit security binding.                                   |
| `performs`             | EntityRelationship       | architecture or actor → function or process                               | Performer to performed work.                                 |
| `hosts`                | EntityRelationship       | platform, container, node, device, or network → architecture              | Host to hosted concept.                                      |
| `connects_to`          | EntityRelationship       | architecture → architecture                                               | Directed exactly as expressed in evidence.                   |
| `calls`                | EntityRelationship       | architecture → architecture                                               | Explicit call between two records.                           |
| `integrates_with`      | EntityRelationship       | architecture → architecture                                               | Directed exactly as expressed in evidence.                   |
| `realizes`             | EntityRelationship       | architecture → business_capability, service, requirement, or function     | Realizing concept to realized concept.                       |
| `used_in`              | EntityRelationship       | business_capability → value_stream                                        | Explicit capability use only.                                |
| `guides`               | EntityRelationship       | standard, control, policy, requirement, or concept → managed              | Guiding artifact to guided concept.                          |
| `owned_by`             | EntityRelationship       | managed → actor                                                           | Explicit ownership only.                                     |
| `owns`                 | EntityRelationship       | actor → managed                                                           | The active voice of `owned_by`.                              |
| `has_custodian`        | EntityRelationship       | managed → actor                                                           | Explicit custodianship only.                                 |
| `operates`             | EntityRelationship       | actor → managed                                                           | Explicit operating responsibility only.                      |
| `maintains`            | EntityRelationship       | actor → managed                                                           | Explicit maintenance responsibility only.                    |
| `member_of`            | EntityRelationship       | person → team or organization                                             | Explicit team membership only.                               |
| `reports_to`           | EntityRelationship       | person → person or role                                                   | Explicit reporting line only.                                |
| `works_on`             | EntityRelationship       | actor → managed                                                           | Explicit work assignment only.                               |
| `tracks`               | EntityRelationship       | process, system, application, or technology → technology_asset            | Explicit tracking only.                                      |
| `tags`                 | EntityRelationship       | process, system, application, or technology → technology_asset            | Explicit asset tagging only.                                 |
| `applies_to`           | EntityRelationship       | license, policy, standard, or control → managed                           | Explicit applicability only.                                 |
| `evidences`            | EntityRelationship       | license, data, or repository → license, requirement, or control           | Explicit proof or evidence only.                             |
| `stores`               | EntityRelationship       | datastore or repository → data or schema                                  | Explicit storage only.                                       |
| `manages`              | EntityRelationship       | actor, process, system, or application → managed                          | Explicit management responsibility only.                     |
| `controlled_by`        | EntityRelationship       | managed → control, standard, policy, or requirement                       | Alias: `control`.                                            |
| `violates`             | EntityRelationship       | managed → control, standard, policy, or requirement                       | Explicit non-compliance only.                                |
| `broader`              | EntityRelationship       | concept, category, or tag → concept, category, or tag                     | Vocabulary relation; inverse: `narrower`.                    |
| `narrower`             | EntityRelationship       | concept, category, or tag → concept, category, or tag                     | Vocabulary relation; inverse: `broader`.                     |
| `related`              | EntityRelationship       | concept, category, or tag to concept, category, or tag                    | Alias: `related_vocabulary`; symmetric.                      |
| `supersedes_entity`    | EntityRelationship       | managed → managed                                                         | Alias: `entity_supersedes`; inverse: `superseded_by_entity`. |
| `superseded_by_entity` | EntityRelationship       | managed → managed                                                         | Inverse: `supersedes_entity`.                                |
| `contradicts`          | IdeaBlockRelation        | IdeaBlock to IdeaBlock                                                    | Symmetric claim relation.                                    |
| `refines`              | IdeaBlockRelation        | IdeaBlock → IdeaBlock                                                     | Claim refinement.                                            |
| `supersedes`           | IdeaBlockRelation        | IdeaBlock → IdeaBlock                                                     | Inverse: `superseded_by`; many-to-one.                       |
| `superseded_by`        | IdeaBlockRelation        | IdeaBlock → IdeaBlock                                                     | Inverse: `supersedes`; many-to-one.                          |
| `semantic_related`     | IdeaBlockRelation        | IdeaBlock to IdeaBlock                                                    | Symmetric claim relation.                                    |
| `equivalent`           | IdeaBlockRelation        | IdeaBlock to IdeaBlock                                                    | Symmetric claim relation.                                    |
| `supersedes`           | GovernanceRecordRelation | adr → adr                                                                 | Explicit ADR replacement; many-to-one.                       |
| `mitigates`            | GovernanceRecordRelation | adr, risk, technical_debt, or architecture_backlog → governance record    | Governance record relation.                                  |
| `waives`               | GovernanceRecordRelation | waiver → governance record                                                | Governance record relation.                                  |
| `incurs`               | GovernanceRecordRelation | adr or architecture_backlog → risk or technical_debt                      | Governance record relation.                                  |
| `resolves`             | GovernanceRecordRelation | risk, waiver, technical_debt, or architecture_backlog → governance record | Governance record relation.                                  |
| `corrects`             | GovernanceRecordRelation | architecture_backlog or technical_debt → governance record                | Governance record relation.                                  |

The curated relationship vocabulary is preserved one-to-one by the Entity relationship rows from `contains` through `manages`. `depends_on`, `produces`, `consumes`, `secured_by`, `controlled_by`, `violates`, the vocabulary relations, Entity supersession, IdeaBlock relations, and governance relations are Margin additions required by the entity-resolution contract. A relation is never created from co-occurrence, a glossary definition, or an unsupported predicate. There is no generic `related_to` fallback.

Ownership is recorded in two directions. `owned_by` reads from the owned record to the party responsible for it, so the owner is an actor — a person, a team, an organization or a role. `owns` reads from the owner to the owned record and admits any record on either side, so a platform can own the services it heads. `calls` records that one record invokes another, which an interface matrix states directly.

## Ontology versions and re-evaluation

The ontology version is part of the record, not only part of the release. Every Entity, EntityRelationship and IdeaBlockRevision carries the ontology version that governed it when it was written, and the version is reported on the records the graph surface returns.

An ontology change is additive: it widens what can be expressed. It must never silently reinterpret history, so the maintenance cycle re-evaluates every record written under a superseded version before that record is read under the new one:

- A record the current registry still supports is restamped at the current version. The registry says the same thing about it, so nothing is lost.
- A record the current registry no longer supports is withdrawn: an Entity is retired, a relationship is marked `stale`, and the reason is raised as a review finding so that a human decides how the record should read.

`margin_db.ontology_review.reevaluate_ontology_records` performs this re-evaluation, and the maintenance health check runs it and reports how many records were restamped and how many were withdrawn. A withdrawn record is never read a new way; it is held for review.

## Reconciliation and extraction rules

The following source-guidance rules are part of the Margin contract:

1. Preserve source name, candidate types, aliases, evidence excerpts, structural context, and provenance while resolving a mention.
2. Treat chunk-level classifications as provisional. Reconcile only when the supplied document context supports the same real-world entity; never invent unseen context.
3. Choose a type by this descending evidence authority: explicit source type, structured classification, unambiguous document structure, constrained relationship endpoint, exact dictionary compound term, definition-supported context, then weak context. Equal-strength material conflicts remain unresolved for review.
4. Use headings, table titles and headers, legends, captions, labels, sheet names, rows, and parent list structure when their association is explicit. Physical proximity alone is insufficient.
5. Split a coordinated field only when it contains independent concepts; never split an official name, supported compound term, or other inseparable concept.
6. An abbreviation or spelling variant becomes an alias only when the source establishes equivalence. A dictionary abbreviation can rank a candidate but cannot automatically merge or create catalog records.
7. Distinguish a type conflict for one entity from a true homonym. Resolve the former by evidence precedence; retain the latter as distinct entities with distinct provenance and context.
8. Keep literal properties source-grounded, attached to the directly described Entity, normalized as snake_case, and free of unsupported nested facts. Preserve a material property conflict for review rather than silently overwriting it.
9. Persist only source-grounded, endpoint-valid binary relationships. Remove exact duplicates and merge-created self-links; do not emit an inverse copy.
10. Resolve automatically only by immutable external ID or one unambiguous exact alias in permitted scope. A model-selected governed `canonical_id`, fuzzy, and contextual matches rank review candidates only. Unknowns remain `EntityMention` review work and never create a canonical Entity during ingestion.
11. A direct source assertion is `extracted`; a contextual interpretation with source evidence is `inferred` and remains reviewable; material uncertainty is `ambiguous` and remains reviewable. Margin deliberately omits confidence-score serialization.

## Source terminology and curation

The complete compact Architecture Dictionary (753 source rows, 750 unique labels) and the two concise glossaries are retained as documentation references:

- [Architecture Dictionary](ontology-source/ARCHITECTURE_DICTIONARY_COMPACT.md)
- [Architecture Glossary](ontology-source/ARCHITECTURE_GLOSSARY_COMPACT.md)
- [Technology Assets Glossary](ontology-source/TECHNOLOGY_ASSETS_GLOSSARY.md)

They provide canonical terminology, definitions, and candidate abbreviations. They do not bypass Entity governance. The two duplicate `Open-Source` and `Value Stream` dictionary rows are one candidate term each; their identical meanings do not justify duplicate entities. The two `Home Subscriber Server` rows have distinct source descriptions and abbreviations, so they remain contextually distinct until a reviewer establishes their identity or alias scope.

## Deliberately excluded legacy behavior

The former external agent role, JSON envelope, deterministic node IDs, confidence-score serialization, graph-fragment merging, and `participate_in` hyperedges are not part of Margin. Margin uses source provenance, IdeaBlock evidence, EntityMention review, and the constrained persistence classes above instead.
