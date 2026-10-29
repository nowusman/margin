import { apiClient } from "./api-client";
import { asArray, rec, str } from "./apiMappers";

export type CanonicalEntity = {
  id: string;
  preferredLabel: string;
  normalizedLabel: string;
  definition: string | null;
  definitionIdeaBlockId: string | null;
  ontologyType: string;
  ontologyVersion: string;
  catalogVersion: number;
  domainId: string | null;
  locale: string;
  externalId: string | null;
  lifecycleStatus: string;
  supersededByEntityId: string | null;
  retiredAt: string | null;
  ownerUserId: string | null;
  stewardUserId: string | null;
  metadata: Record<string, unknown>;
  createdAt: string | null;
  updatedAt: string | null;
};

export type CanonicalEntityAlias = {
  id: string;
  alias: string;
  normalizedAlias: string;
  locale: string;
  domainId: string | null;
  ontologyType: string;
  lifecycleStatus: string;
  sourceProvenance: Record<string, unknown>;
};

export type EntityResolutionHistory = {
  id: string;
  mentionId: string;
  entityId: string | null;
  outcome: string;
  method: string;
  confidence: number;
  candidateEntityIds: string[];
  rationale: string | null;
  status: string;
  ontologyVersion: string;
  decidedBy: string | null;
  isCurrent: boolean;
  createdAt: string | null;
  reviewedAt: string | null;
};

export type UnresolvedEntityMention = {
  id: string;
  text: string;
  proposedType: string;
  domainId: string;
  sourceElementId: string;
  ideaBlockRevisionId: string;
  candidateEntityIds: string[];
  ontologyVersion: string;
  decision: EntityResolutionHistory;
};

export type EntityCatalogDetail = CanonicalEntity & {
  aliases: CanonicalEntityAlias[];
  ideaBlocks: Array<{ ideaBlockId: string; role: string; currentRevisionId: string | null }>;
  governanceRecords: Array<{ recordType: string; entityId: string }>;
  sourceMentions: Array<{
    mentionId: string;
    text: string;
    sourceElementId: string;
    ideaBlockRevisionId: string;
  }>;
  resolutionHistory: EntityResolutionHistory[];
};

export type EntityCatalogList = {
  items: CanonicalEntity[];
  count: number;
  unresolvedMentions: UnresolvedEntityMention[];
  unresolvedCount: number;
};

export type EntityCatalogFilters = {
  domainId?: string;
  ontologyType?: string;
  lifecycleStatus?: string;
  alias?: string;
  unresolved?: boolean;
  limit?: number;
};

export type EntityCatalogOntology = {
  ontologyVersion: string;
  entityTypes: string[];
  lifecycleStatuses: string[];
};

export type EntityCreateInput = {
  preferredLabel: string;
  ontologyType: string;
  domainId?: string | null;
  locale?: string;
  definition?: string | null;
  definitionIdeaBlockId?: string | null;
  externalId?: string | null;
  lifecycleStatus?: string;
  ownerUserId?: string | null;
  stewardUserId?: string | null;
};

export type EntityUpdateInput = Partial<Omit<EntityCreateInput, "domainId">> & {
  reason: string;
};

const nullableString = (value: unknown): string | null =>
  typeof value === "string" ? value : null;

const entity = (value: unknown): CanonicalEntity => {
  const row = rec(value);
  return {
    id: str(row.id),
    preferredLabel: str(row.preferred_label),
    normalizedLabel: str(row.normalized_label),
    definition: nullableString(row.definition),
    definitionIdeaBlockId: nullableString(row.definition_idea_block_id),
    ontologyType: str(row.ontology_type),
    ontologyVersion: str(row.ontology_version),
    catalogVersion: typeof row.catalog_version === "number" ? row.catalog_version : 1,
    domainId: nullableString(row.domain_id),
    locale: str(row.locale, "en"),
    externalId: nullableString(row.external_id),
    lifecycleStatus: str(row.lifecycle_status, "draft"),
    supersededByEntityId: nullableString(row.superseded_by_entity_id),
    retiredAt: nullableString(row.retired_at),
    ownerUserId: nullableString(row.owner_user_id),
    stewardUserId: nullableString(row.steward_user_id),
    metadata: rec(row.metadata),
    createdAt: nullableString(row.created_at),
    updatedAt: nullableString(row.updated_at),
  };
};

const alias = (value: unknown): CanonicalEntityAlias => {
  const row = rec(value);
  return {
    id: str(row.id),
    alias: str(row.alias),
    normalizedAlias: str(row.normalized_alias),
    locale: str(row.locale, "en"),
    domainId: nullableString(row.domain_id),
    ontologyType: str(row.ontology_type),
    lifecycleStatus: str(row.lifecycle_status, "active"),
    sourceProvenance: rec(row.source_provenance),
  };
};

const decision = (value: unknown): EntityResolutionHistory => {
  const row = rec(value);
  return {
    id: str(row.id),
    mentionId: str(row.mention_id),
    entityId: nullableString(row.entity_id),
    outcome: str(row.outcome),
    method: str(row.method),
    confidence: typeof row.confidence === "number" ? row.confidence : 0,
    candidateEntityIds: asArray<string>(row.candidate_entity_ids),
    rationale: nullableString(row.rationale),
    status: str(row.status),
    ontologyVersion: str(row.ontology_version),
    decidedBy: nullableString(row.decided_by),
    isCurrent: row.is_current === true,
    createdAt: nullableString(row.created_at),
    reviewedAt: nullableString(row.reviewed_at),
  };
};

const unresolvedMention = (value: unknown): UnresolvedEntityMention => {
  const row = rec(value);
  return {
    id: str(row.id),
    text: str(row.text),
    proposedType: str(row.proposed_type),
    domainId: str(row.domain_id),
    sourceElementId: str(row.source_element_id),
    ideaBlockRevisionId: str(row.idea_block_revision_id),
    candidateEntityIds: asArray<string>(row.candidate_entity_ids),
    ontologyVersion: str(row.ontology_version),
    decision: decision(row.decision),
  };
};

const detail = (value: unknown): EntityCatalogDetail => {
  const row = rec(value);
  return {
    ...entity(row),
    aliases: asArray(row.aliases).map(alias),
    ideaBlocks: asArray<Record<string, unknown>>(row.idea_blocks).map((item) => ({
      ideaBlockId: str(item.idea_block_id),
      role: str(item.role, "context"),
      currentRevisionId: nullableString(item.current_revision_id),
    })),
    governanceRecords: asArray<Record<string, unknown>>(row.governance_records).map((item) => ({
      recordType: str(item.record_type),
      entityId: str(item.entity_id),
    })),
    sourceMentions: asArray<Record<string, unknown>>(row.source_mentions).map((item) => ({
      mentionId: str(item.mention_id),
      text: str(item.text),
      sourceElementId: str(item.source_element_id),
      ideaBlockRevisionId: str(item.idea_block_revision_id),
    })),
    resolutionHistory: asArray(row.resolution_history).map(decision),
  };
};

const listQuery = (filters: EntityCatalogFilters): string => {
  const query = new URLSearchParams();
  if (filters.domainId) query.set("domain_id", filters.domainId);
  if (filters.ontologyType) query.set("ontology_type", filters.ontologyType);
  if (filters.lifecycleStatus) query.set("lifecycle_status", filters.lifecycleStatus);
  if (filters.alias) query.set("alias", filters.alias);
  if (filters.unresolved) query.set("unresolved", "true");
  if (filters.limit) query.set("limit", String(filters.limit));
  const value = query.toString();
  return value ? `?${value}` : "";
};

const createPayload = (input: EntityCreateInput): Record<string, unknown> => ({
  preferred_label: input.preferredLabel,
  ontology_type: input.ontologyType,
  domain_id: input.domainId ?? null,
  locale: input.locale ?? "en",
  definition: input.definition ?? null,
  definition_idea_block_id: input.definitionIdeaBlockId ?? null,
  external_id: input.externalId ?? null,
  lifecycle_status: input.lifecycleStatus ?? "draft",
  owner_user_id: input.ownerUserId ?? null,
  steward_user_id: input.stewardUserId ?? null,
});

const updatePayload = (input: EntityUpdateInput): Record<string, unknown> => {
  const payload: Record<string, unknown> = { reason: input.reason };
  if (input.preferredLabel !== undefined) payload.preferred_label = input.preferredLabel;
  if (input.ontologyType !== undefined) payload.ontology_type = input.ontologyType;
  if (input.locale !== undefined) payload.locale = input.locale;
  if (input.definition !== undefined) payload.definition = input.definition;
  if (input.definitionIdeaBlockId !== undefined)
    payload.definition_idea_block_id = input.definitionIdeaBlockId;
  if (input.externalId !== undefined) payload.external_id = input.externalId;
  if (input.lifecycleStatus !== undefined) payload.lifecycle_status = input.lifecycleStatus;
  if (input.ownerUserId !== undefined) payload.owner_user_id = input.ownerUserId;
  if (input.stewardUserId !== undefined) payload.steward_user_id = input.stewardUserId;
  return payload;
};

export const entityCatalogClient = {
  list: async (filters: EntityCatalogFilters = {}): Promise<EntityCatalogList> => {
    const body = await apiClient.get<Record<string, unknown>>(`/api/entities${listQuery(filters)}`);
    return {
      items: asArray(body.items).map(entity),
      count: typeof body.count === "number" ? body.count : 0,
      unresolvedMentions: asArray(body.unresolved_mentions).map(unresolvedMention),
      unresolvedCount: typeof body.unresolved_count === "number" ? body.unresolved_count : 0,
    };
  },
  ontology: async (): Promise<EntityCatalogOntology> => {
    const body = await apiClient.get<Record<string, unknown>>("/api/entities/catalog/ontology");
    return {
      ontologyVersion: str(body.ontology_version),
      entityTypes: asArray<string>(body.entity_types),
      lifecycleStatuses: asArray<string>(body.lifecycle_statuses),
    };
  },
  detail: async (entityId: string): Promise<EntityCatalogDetail> =>
    detail(await apiClient.get(`/api/entities/${encodeURIComponent(entityId)}`)),
  create: async (input: EntityCreateInput): Promise<CanonicalEntity> =>
    entity(await apiClient.post("/api/entities", createPayload(input))),
  update: async (entityId: string, input: EntityUpdateInput): Promise<EntityCatalogDetail> =>
    detail(
      await apiClient.patch(`/api/entities/${encodeURIComponent(entityId)}`, updatePayload(input))
    ),
  approve: async (entityId: string, reason: string): Promise<CanonicalEntity> =>
    entity(
      await apiClient.post(`/api/entities/${encodeURIComponent(entityId)}/approve`, { reason })
    ),
  addAlias: async (
    entityId: string,
    aliasValue: string,
    reason: string,
    sourceProvenance: Record<string, unknown> = { kind: "catalog_governance" }
  ): Promise<CanonicalEntityAlias> =>
    alias(
      await apiClient.post(`/api/entities/${encodeURIComponent(entityId)}/aliases`, {
        alias: aliasValue,
        reason,
        source_provenance: sourceProvenance,
      })
    ),
  deprecateAlias: async (
    entityId: string,
    aliasId: string,
    reason: string
  ): Promise<CanonicalEntityAlias> =>
    alias(
      await apiClient.post(
        `/api/entities/${encodeURIComponent(entityId)}/aliases/${encodeURIComponent(aliasId)}/deprecate`,
        { reason }
      )
    ),
  merge: (entityId: string, survivorId: string, reason: string): Promise<Record<string, unknown>> =>
    apiClient.post(`/api/entities/${encodeURIComponent(entityId)}/merge`, {
      target_entity_id: survivorId,
      reason,
    }),
  supersede: (
    entityId: string,
    successorId: string,
    reason: string
  ): Promise<Record<string, unknown>> =>
    apiClient.post(`/api/entities/${encodeURIComponent(entityId)}/supersede`, {
      target_entity_id: successorId,
      reason,
    }),
  retire: async (entityId: string, reason: string): Promise<CanonicalEntity> =>
    entity(
      await apiClient.post(`/api/entities/${encodeURIComponent(entityId)}/retire`, { reason })
    ),
  unresolvedMentions: async (domainId?: string): Promise<UnresolvedEntityMention[]> => {
    const query = domainId ? `?domain_id=${encodeURIComponent(domainId)}` : "";
    const body = await apiClient.get<Record<string, unknown>>(`/api/entities/mentions${query}`);
    return asArray(body.items).map(unresolvedMention);
  },
  resolveMention: async (
    mentionId: string,
    entityId: string,
    reason: string
  ): Promise<EntityResolutionHistory> =>
    decision(
      await apiClient.post(`/api/entities/mentions/${encodeURIComponent(mentionId)}/resolve`, {
        entity_id: entityId,
        reason,
      })
    ),
  sourceEntities: async (sourceId: string): Promise<CanonicalEntity[]> => {
    const body = await apiClient.get<Record<string, unknown>>(
      `/api/sources/${encodeURIComponent(sourceId)}/entities`
    );
    return asArray(body.entities).map(entity);
  },
};
