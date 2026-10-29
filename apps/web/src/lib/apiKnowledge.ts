import { ApiError, apiClient } from "./api-client";
import { entityCatalogClient } from "./apiEntities";
import { AnyRecord, asArray, bool, diagramSummary, num, rec, str } from "./apiMappers";
import { fallbackName, shortId } from "./display";
import type {
  C4Node,
  C4Overlay,
  DiagramSummary,
  DomainSummary,
  EntitySummary,
  ExplicitRelationshipEdge,
  GovernedEntityRelationshipResult,
  IdeaBlockRelationship,
  IdeaBlockRelationshipResult,
  IdeaBlockSummary,
  ManualIdeaBlockInput,
  RelationshipEntity,
  RelationshipImpactResult,
  RelationshipNeighborResult,
  RelationshipPathResult,
} from "./apiTypes";

const nullableString = (value: unknown): string | null =>
  typeof value === "string" ? value : null;

const relationshipRequest = async <T>(request: () => Promise<T>): Promise<T> => {
  try {
    return await request();
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    if (error.status === 403)
      throw new Error("You do not have permission to view these relationships.");
    if (error.status === 422)
      throw new Error(
        "The relationship query is invalid. Choose valid entities and a bounded depth."
      );
    if (error.status === 503)
      throw new Error(
        "Dependency paths and impact paths require the PostgreSQL relationship service."
      );
    if (error.status === 504)
      throw new Error(
        "The relationship query timed out. Narrow the path or choose a smaller depth."
      );
    throw error;
  }
};

const domainSummary = (value: unknown): DomainSummary => {
  const item = rec(value);
  const id = str(item.id);
  return {
    id,
    name: fallbackName(str(item.name), `Domain ${shortId(id)}`),
    owner: str(item.owner),
    steward: str(item.steward),
    status: str(item.status, "unknown"),
    description: str(item.description),
  };
};

const uniqueRealDomains = (domains: DomainSummary[]): DomainSummary[] => {
  const seen = new Set<string>();
  return domains.filter((domain) => {
    const values = [domain.id, domain.name].map((value) => value.trim().toLowerCase());
    if (
      values.some((value) => value === "all" || value === "all domains" || value === "all_domains")
    )
      return false;
    const key = (domain.id || domain.name).trim().toLowerCase();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
};

const entitySummary = (value: unknown): EntitySummary => {
  const item = rec(value);
  const id = str(item.id);
  return {
    id,
    name: fallbackName(str(item.preferred_label, str(item.name)), `Entity ${shortId(id)}`),
    kind: str(item.ontology_type, str(item.kind)),
    category: str(item.category),
    domainId: str(item.domain_id),
    owner: str(item.owner_user_id, str(item.owner)),
    steward: str(item.steward_user_id, str(item.steward)),
    status: str(item.lifecycle_status, str(item.status, "unknown")),
    description: str(item.definition, str(item.description)),
    metadata: rec(item.metadata),
  };
};

export const ideaBlockSummary = (value: unknown): IdeaBlockSummary => {
  const item = rec(value);
  return {
    id: str(item.id),
    domainId: str(item.domain_id),
    currentRevisionId: str(item.current_revision_id),
    statement: str(item.statement),
    category: str(item.category),
    type: str(item.type),
    confidence: str(item.confidence),
    status: str(item.status),
    revisionNumber: num(item.revision_number),
    publicationStatus: str(item.publication_status, str(item.status)),
    revisions: asArray<AnyRecord>(item.revisions).map((revision) => ({
      id: str(revision.id),
      revisionNumber: num(revision.revision_number),
      statement: str(revision.statement),
      normalizedStatement: str(revision.normalized_statement),
      type: str(revision.type),
      confidence: str(revision.confidence),
      polarity: str(revision.polarity),
      qualifiers: rec(revision.qualifiers),
      validFrom: nullableString(revision.valid_from),
      validTo: nullableString(revision.valid_to),
      publicationStatus: str(revision.publication_status),
      ontologyVersion: str(revision.ontology_version),
      actor: str(revision.actor),
      process: str(revision.process),
      changeReason: nullableString(revision.change_reason),
      createdAt: nullableString(revision.created_at),
    })),
    evidence: asArray<AnyRecord>(item.evidence).map((occurrence) => ({
      id: str(occurrence.id),
      ideaBlockRevisionId: str(occurrence.idea_block_revision_id),
      sourcePageId: nullableString(occurrence.source_page_id),
      sourceElementId: str(occurrence.source_element_id),
      sourceQuote: str(occurrence.source_quote),
      exactQuoteStart:
        typeof occurrence.exact_quote_start === "number" ? occurrence.exact_quote_start : null,
      exactQuoteEnd:
        typeof occurrence.exact_quote_end === "number" ? occurrence.exact_quote_end : null,
      evidenceKind: str(occurrence.evidence_kind),
      polarity: str(occurrence.polarity),
      status: str(occurrence.status),
      redacted: bool(occurrence.redacted),
      redactionReason: nullableString(occurrence.redaction_reason),
    })),
    relations: asArray<AnyRecord>(item.relations).map((relation) => ({
      id: str(relation.id),
      sourceIdeaBlockId: str(relation.source_idea_block_id),
      targetIdeaBlockId: str(relation.target_idea_block_id),
      relationType: str(relation.relation_type),
      direction: str(relation.direction) as "directed" | "symmetric",
      status: str(relation.status),
      validFrom: nullableString(relation.valid_from),
      validTo: nullableString(relation.valid_to),
      confidence: num(relation.confidence),
      decisionProvenance: rec(relation.decision_provenance),
    })),
    metadata: rec(item.metadata),
  };
};

const relationshipEntity = (value: unknown): RelationshipEntity => {
  const entity = rec(value);
  const id = str(entity.id);
  return {
    id,
    label: fallbackName(str(entity.label), `Entity ${shortId(id)}`),
    ontologyType: str(entity.ontology_type),
    domainId: str(entity.domain_id),
    lifecycleStatus: str(entity.lifecycle_status),
  };
};

const explicitRelationshipEdge = (value: unknown): ExplicitRelationshipEdge => {
  const edge = rec(value);
  return {
    relationshipId: str(edge.relationship_id),
    source: relationshipEntity(edge.source),
    target: relationshipEntity(edge.target),
    relationType: str(edge.relation_type),
    confidence: str(edge.confidence),
    status: str(edge.status),
    validFrom: nullableString(edge.valid_from),
    validTo: nullableString(edge.valid_to),
    direction: str(edge.direction),
    hop: num(edge.hop),
    provenance: asArray<AnyRecord>(edge.provenance).map((provenance) => ({
      id: str(provenance.id),
      ideaBlockId: str(provenance.idea_block_id),
      sourceElementId: str(provenance.source_element_id),
      sourceQuote: nullableString(provenance.source_quote),
      polarity: str(provenance.polarity),
      status: str(provenance.status),
      validFrom: nullableString(provenance.valid_from),
      validTo: nullableString(provenance.valid_to),
    })),
  };
};

const ideaBlockRelationship = (value: unknown): IdeaBlockRelationship => {
  const relation = rec(value);
  const block = (item: unknown) => {
    const ideaBlock = rec(item);
    return {
      id: str(ideaBlock.id),
      statement: str(ideaBlock.statement),
      type: str(ideaBlock.type),
      status: str(ideaBlock.status),
      domainId: str(ideaBlock.domain_id),
    };
  };
  return {
    relationshipId: str(relation.relationship_id),
    source: block(relation.source),
    target: block(relation.target),
    relationType: str(relation.relation_type),
    direction: str(relation.direction),
    status: str(relation.status),
    confidence: num(relation.confidence),
    validFrom: nullableString(relation.valid_from),
    validTo: nullableString(relation.valid_to),
    decisionProvenance: rec(relation.decision_provenance),
  };
};

const listEntities = async (): Promise<EntitySummary[]> =>
  (await entityCatalogClient.list({ limit: 250 })).items.map(entitySummary);
const listDomains = async (permission = "read"): Promise<DomainSummary[]> =>
  uniqueRealDomains(
    asArray<AnyRecord>(
      (
        await apiClient.get<{ domains: unknown[] }>(
          `/api/domains?permission=${encodeURIComponent(permission)}`
        )
      ).domains
    ).map(domainSummary)
  );
const saveDomain = async (payload: {
  id: string;
  name: string;
  owner?: string;
  steward?: string;
  description?: string;
}): Promise<DomainSummary | null> => {
  const body = await apiClient.post<{ domain?: unknown }>("/api/domains", payload);
  return body.domain ? domainSummary(body.domain) : null;
};
const deleteDomain = (domainId: string): Promise<{ status: string }> =>
  apiClient.delete(`/api/domains/${encodeURIComponent(domainId)}`);

export const knowledgeClient = {
  listDomains,
  saveDomain,
  deleteDomain,
  listEntities,
  listSourceEntities: async (sourceId: string): Promise<EntitySummary[]> =>
    (await entityCatalogClient.sourceEntities(sourceId)).map(entitySummary),
  getEntity: async (entityId: string): Promise<EntitySummary | null> =>
    entitySummary(await entityCatalogClient.detail(entityId)),
  getEntityEvidence: async (entityId: string): Promise<IdeaBlockSummary[]> => {
    const detail = await entityCatalogClient.detail(entityId);
    const records = await Promise.all(
      detail.ideaBlocks.map((link) =>
        apiClient.get<{ idea_block: unknown }>(
          `/api/ideablocks/${encodeURIComponent(link.ideaBlockId)}`
        )
      )
    );
    return records
      .filter((record) => Boolean(record.idea_block))
      .map((record) => ideaBlockSummary(record.idea_block));
  },
  listIdeaBlocks: async (): Promise<IdeaBlockSummary[]> =>
    asArray<AnyRecord>(
      (await apiClient.get<{ idea_blocks: unknown[] }>("/api/ideablocks")).idea_blocks
    ).map(ideaBlockSummary),
  createIdeaBlock: async (payload: ManualIdeaBlockInput): Promise<IdeaBlockSummary> => {
    const body = await apiClient.post<{ idea_block: unknown }>("/api/ideablocks", {
      statement: payload.statement,
      domain_id: payload.domainId,
      category: payload.category,
      type: payload.type,
      confidence: payload.confidence,
      source_quote: payload.sourceQuote,
      entities: payload.entities.map((entity) => ({
        name: entity.name,
        kind: entity.kind,
        role: entity.role,
        external_id: entity.externalId,
      })),
      relationships: payload.relationships.map((relationship) => ({
        source: relationship.source,
        relation_type: relationship.relationType,
        target: relationship.target,
        confidence: relationship.confidence,
      })),
    });
    return ideaBlockSummary(body.idea_block);
  },
  listSourceIdeaBlocks: async (sourceId: string): Promise<IdeaBlockSummary[]> =>
    asArray<AnyRecord>(
      (
        await apiClient.get<{ idea_blocks: unknown[] }>(
          `/api/sources/${encodeURIComponent(sourceId)}/ideablocks`
        )
      ).idea_blocks
    ).map(ideaBlockSummary),
  getIdeaBlock: async (ideaBlockId: string): Promise<IdeaBlockSummary | null> => {
    const body = await apiClient.get<{ idea_block: unknown }>(
      `/api/ideablocks/${encodeURIComponent(ideaBlockId)}`
    );
    return body.idea_block ? ideaBlockSummary(body.idea_block) : null;
  },
  updateIdeaBlock: async (
    ideaBlockId: string,
    payload: { statement?: string; status?: string }
  ): Promise<IdeaBlockSummary | null> => {
    const body = await apiClient.patch<{ idea_block: unknown }>(
      `/api/ideablocks/${encodeURIComponent(ideaBlockId)}`,
      payload
    );
    return body.idea_block ? ideaBlockSummary(body.idea_block) : null;
  },
  updateIdeaBlockStatus: (ideaBlockId: string, status: string): Promise<IdeaBlockSummary | null> =>
    knowledgeClient.updateIdeaBlock(ideaBlockId, { status }),
  deleteIdeaBlock: (ideaBlockId: string): Promise<{ status: string; idea_block_id: string }> =>
    apiClient.delete(`/api/ideablocks/${encodeURIComponent(ideaBlockId)}`),
  relationshipNeighbors: async (
    entityId: string,
    options: {
      relationTypes?: string[];
      includeCandidates?: boolean;
      asOf?: string;
      limit?: number;
    } = {}
  ): Promise<RelationshipNeighborResult> => {
    const params = new URLSearchParams();
    options.relationTypes?.forEach((type) => params.append("relation_types", type));
    if (options.includeCandidates) params.set("include_candidates", "true");
    if (options.asOf) params.set("as_of", options.asOf);
    if (options.limit) params.set("limit", String(options.limit));
    const query = params.toString();
    const body = await relationshipRequest(() =>
      apiClient.get<AnyRecord>(
        `/api/relationships/neighbors/${encodeURIComponent(entityId)}${query ? `?${query}` : ""}`
      )
    );
    return {
      entityId: str(body.entity_id),
      edges: asArray(body.edges).map(explicitRelationshipEdge),
      capped: bool(body.capped),
    };
  },
  relationshipPath: async (input: {
    sourceEntityId: string;
    targetEntityId: string;
    maxDepth: number;
    relationTypes?: string[];
    includeCandidates?: boolean;
    asOf?: string;
  }): Promise<RelationshipPathResult> => {
    const body = await relationshipRequest(() =>
      apiClient.post<AnyRecord>("/api/relationships/path", {
        source_entity_id: input.sourceEntityId,
        target_entity_id: input.targetEntityId,
        max_depth: input.maxDepth,
        relation_types: input.relationTypes,
        include_candidates: input.includeCandidates ?? false,
        as_of: input.asOf ?? null,
      })
    );
    return {
      sourceEntityId: str(body.source_entity_id),
      targetEntityId: str(body.target_entity_id),
      path: asArray(body.path).map(explicitRelationshipEdge),
      found: bool(body.found),
      capped: bool(body.capped),
    };
  },
  relationshipImpact: async (
    entityId: string,
    options: {
      maxDepth: number;
      direction: "inbound" | "outbound" | "both";
      relationTypes?: string[];
      includeCandidates?: boolean;
      asOf?: string;
    }
  ): Promise<RelationshipImpactResult> => {
    const params = new URLSearchParams({
      max_depth: String(options.maxDepth),
      direction: options.direction,
    });
    options.relationTypes?.forEach((type) => params.append("relation_types", type));
    if (options.includeCandidates) params.set("include_candidates", "true");
    if (options.asOf) params.set("as_of", options.asOf);
    const body = await relationshipRequest(() =>
      apiClient.get<AnyRecord>(
        `/api/relationships/impact/${encodeURIComponent(entityId)}?${params.toString()}`
      )
    );
    return {
      entityId: str(body.entity_id),
      impacts: asArray<AnyRecord>(body.impacts).map((impact) => ({
        entity: relationshipEntity(impact.entity),
        hop: num(impact.hop),
        relationType: nullableString(impact.relation_type),
        path: asArray(impact.path).map(explicitRelationshipEdge),
      })),
      capped: bool(body.capped),
    };
  },
  governedEntityRelationships: async (
    entityId: string,
    options: { limit?: number } = {}
  ): Promise<GovernedEntityRelationshipResult> => {
    const query = options.limit ? `?limit=${encodeURIComponent(String(options.limit))}` : "";
    const body = await relationshipRequest(() =>
      apiClient.get<AnyRecord>(
        `/api/relationships/governed-entity/${encodeURIComponent(entityId)}${query}`
      )
    );
    return {
      relationships: asArray<AnyRecord>(body.governance_records).map((record) => ({
        governanceRecordId: str(record.governance_record_id),
        businessKey: str(record.business_key),
        title: str(record.title),
        recordType: str(record.record_type),
        lifecycleStatus: str(record.lifecycle_status),
        domainId: str(record.domain_id),
        role: str(record.role),
      })),
      capped: bool(body.capped),
    };
  },
  relatedIdeaBlockRelationships: async (
    ideaBlockId: string,
    options: { includeCandidates?: boolean; asOf?: string; limit?: number } = {}
  ): Promise<IdeaBlockRelationshipResult> => {
    const params = new URLSearchParams();
    if (options.includeCandidates) params.set("include_candidates", "true");
    if (options.asOf) params.set("as_of", options.asOf);
    if (options.limit) params.set("limit", String(options.limit));
    const query = params.toString();
    const body = await relationshipRequest(() =>
      apiClient.get<AnyRecord>(
        `/api/relationships/related-idea-block/${encodeURIComponent(ideaBlockId)}${query ? `?${query}` : ""}`
      )
    );
    return {
      ideaBlockId: str(body.idea_block_id),
      relations: asArray(body.relations).map(ideaBlockRelationship),
      capped: bool(body.capped),
    };
  },
  c4Tree: async (): Promise<C4Node[]> =>
    asArray<AnyRecord>((await apiClient.get<{ nodes: unknown[] }>("/api/c4/tree")).nodes).map(
      (node) => ({
        id: str(node.id),
        level: str(node.level, str(node.kind, "entity")),
        name: str(node.name, str(node.id)),
      })
    ),
  c4Overlays: async (): Promise<C4Overlay[]> =>
    asArray<AnyRecord>(
      (await apiClient.get<{ overlays: unknown[] }>("/api/c4/overlays")).overlays
    ).map((overlay) => ({
      id: str(overlay.id),
      type: str(overlay.type),
      entity: str(overlay.entity, str(overlay.id)),
    })),
  diagrams: async (): Promise<DiagramSummary[]> =>
    asArray<AnyRecord>(
      (await apiClient.get<{ diagrams: unknown[] }>("/api/diagrams")).diagrams
    ).map(diagramSummary),
  diagram: async (diagramId: string): Promise<DiagramSummary> =>
    diagramSummary(await apiClient.get<unknown>(`/api/diagrams/${encodeURIComponent(diagramId)}`)),
};
