import { apiClient } from "./api-client";
import { AnyRecord, asArray, bool, num, rec, str } from "./apiMappers";
import { ideaBlockSummary } from "./apiKnowledge";
import type {
  AskRequest,
  AskResponse,
  RetrievalAmbiguity,
  RetrievalCitation,
  RetrievalFilterOptions,
  RetrievalFilters,
  RetrievalResponse,
  RetrievalResultCard,
  RetrievalFusion,
  SearchRequest,
} from "./apiTypes";

const ASK_TIMEOUT_MS = 120_000;
const nullableNumber = (value: unknown): number | null =>
  typeof value === "number" ? value : null;
const nullableString = (value: unknown): string | null =>
  typeof value === "string" ? value : null;
const nullableInteger = (value: unknown): number | null =>
  typeof value === "number" && Number.isInteger(value) ? value : null;

const retrievalBody = (request: SearchRequest): AnyRecord => ({
  query: request.query,
  filters: {
    domain_ids: request.filters.domainIds,
    category_ids: request.filters.categoryIds,
    entity_ids: request.filters.entityIds,
    entity_terms: request.filters.entityTerms,
    entity_types: request.filters.entityTypes,
    relationship_types: request.filters.relationshipTypes,
    idea_block_types: request.filters.ideaBlockTypes,
    source_document_ids: request.filters.sourceDocumentIds,
    sensitivities: request.filters.sensitivities,
    statuses: request.filters.statuses,
    valid_from: request.filters.validFrom,
    valid_to: request.filters.validTo,
    governance_record_types: request.filters.governanceRecordTypes,
    governance_statuses: request.filters.governanceStatuses,
    architecture_backlog_states: request.filters.architectureBacklogStates,
  },
  top_k: Math.max(1, Math.min(20, request.topK)),
  expand: {
    relation_types: request.expand.relationTypes,
    depth: Math.max(0, Math.min(2, request.expand.depth)),
    limit: Math.max(0, Math.min(20, request.expand.limit)),
  },
  output_language: request.outputLanguage ?? "",
  context_budget_chars: request.contextBudgetChars ?? null,
  thread_id: request.threadId ?? null,
});

const emptyRetrievalFilters = (): RetrievalFilters => ({
  domainIds: [],
  categoryIds: [],
  entityIds: [],
  entityTerms: [],
  entityTypes: [],
  relationshipTypes: [],
  ideaBlockTypes: [],
  sourceDocumentIds: [],
  sensitivities: [],
  statuses: [],
  validFrom: null,
  validTo: null,
  governanceRecordTypes: [],
  governanceStatuses: [],
  architectureBacklogStates: [],
});
const retrievalFilters = (value: unknown): RetrievalFilters => {
  const filters = rec(value);
  return {
    ...emptyRetrievalFilters(),
    domainIds: asArray<string>(filters.domain_ids),
    categoryIds: asArray<string>(filters.category_ids),
    entityIds: asArray<string>(filters.entity_ids),
    entityTerms: asArray<string>(filters.entity_terms),
    entityTypes: asArray<string>(filters.entity_types),
    relationshipTypes: asArray<string>(filters.relationship_types),
    ideaBlockTypes: asArray<string>(filters.idea_block_types),
    sourceDocumentIds: asArray<string>(filters.source_document_ids),
    sensitivities: asArray<string>(filters.sensitivities),
    statuses: asArray<string>(filters.statuses),
    validFrom: nullableString(filters.valid_from),
    validTo: nullableString(filters.valid_to),
    governanceRecordTypes: asArray<string>(filters.governance_record_types),
    governanceStatuses: asArray<string>(filters.governance_statuses),
    architectureBacklogStates: asArray<string>(filters.architecture_backlog_states),
  };
};
const retrievalCitation = (value: unknown): RetrievalCitation => {
  const citation = rec(value);
  const boundingBox = rec(citation.bounding_box);
  return {
    id: str(citation.citation_id, str(citation.id)),
    ideaBlockId: str(citation.idea_block_id),
    ideaBlockRevisionId: str(citation.idea_block_revision_id),
    sourceDocumentId: str(citation.source_document_id),
    sourceTitle: str(
      citation.source_document_title,
      str(citation.source_document_id, "Unknown source")
    ),
    sourceVersionId: str(citation.source_version_id),
    sourceVersionSha256: str(citation.source_version_sha256),
    sourcePageId: str(citation.source_page_id),
    sourceElementId: str(citation.source_element_id),
    pageNumber: nullableNumber(citation.page_number),
    boundingBox: Object.keys(boundingBox).length ? boundingBox : null,
    quote: str(citation.quote),
    quoteCharStart: nullableNumber(citation.quote_char_start),
    quoteCharEnd: nullableNumber(citation.quote_char_end),
    elementType: str(citation.element_type),
    polarity: str(citation.polarity),
    validFrom: nullableString(citation.valid_from),
    validTo: nullableString(citation.valid_to),
    governanceRecordId: str(citation.governance_record_id),
    governanceRecordRevisionId: str(citation.governance_record_revision_id),
    governanceRecordType: str(citation.governance_record_type),
    assetKey: str(citation.asset_key),
    redacted: bool(citation.redacted),
    redactionReason: str(citation.redaction_reason),
    entityId: str(citation.entity_id),
    entityName: str(citation.entity_name),
    confidence: str(citation.confidence, "unknown"),
  };
};
const retrievalResult = (value: unknown): RetrievalResultCard => {
  const result = rec(value);
  return {
    ideaBlockId: str(result.idea_block_id),
    ideaBlockRevisionId: str(result.idea_block_revision_id),
    score: num(result.score),
    statement: str(result.statement),
    snippet: str(result.snippet, str(result.statement)),
    type: str(result.type),
    status: str(result.status),
    polarity: str(result.polarity),
    validFrom: nullableString(result.valid_from),
    validTo: nullableString(result.valid_to),
    entities: asArray<AnyRecord>(result.entities).map((entity) => ({
      id: str(entity.id),
      name: str(entity.name, str(entity.preferred_label)),
      type: str(entity.type, str(entity.ontology_type)),
    })),
    citations: asArray(result.citations).map(retrievalCitation),
    contradictions: asArray<AnyRecord>(result.contradictions).map((relation) => ({
      id: str(relation.id),
      relationType: str(relation.relation_type),
      status: str(relation.status),
    })),
    supersession: asArray<AnyRecord>(result.supersession).map((relation) => ({
      id: str(relation.id),
      relationType: str(relation.relation_type),
      status: str(relation.status),
    })),
    governanceRecords: asArray<AnyRecord>(result.governance_records).map((record) => ({
      id: str(record.id),
      type: str(record.type, str(record.record_type)),
      status: str(record.status, str(record.lifecycle_status)),
      title: str(record.title),
    })),
    related: asArray<AnyRecord>(result.related).map((record) => ({
      kind: str(record.kind),
      id: str(record.id),
      label: str(record.label),
      type: str(record.type),
    })),
  };
};
const retrievalFusion = (value: unknown): RetrievalFusion => {
  const fusion = rec(value);
  const channels: Record<string, number> = {};
  const raw = rec(fusion.channels);
  Object.entries(raw).forEach(([channel, count]) => {
    channels[channel] = typeof count === "number" ? count : num(count);
  });
  return {
    mode: str(fusion.mode),
    channels,
    count: num(fusion.count),
  };
};
const retrievalAmbiguity = (value: unknown): RetrievalAmbiguity => ({
  entityTerms: asArray<AnyRecord>(rec(value).entity_terms).map((item) => ({
    term: str(item.term),
    candidateEntityIds: asArray<string>(item.candidate_entity_ids),
    candidateTypes: asArray<string>(item.candidate_types),
  })),
});
const retrievalResponse = (payload: unknown): RetrievalResponse => {
  const body = rec(payload);
  const requestedMode = str(body.response_mode, "search_only");
  const stageTimings = rec(body.stage_timings);
  const expansion = rec(body.expansion);
  return {
    contractVersion: "v1",
    responseMode:
      requestedMode === "hybrid" ||
      requestedMode === "lexical_only" ||
      requestedMode === "search_only"
        ? requestedMode
        : "search_only",
    results: asArray(body.results).map(retrievalResult),
    fusion: retrievalFusion(body.fusion),
    expansion: {
      status:
        expansion.status === "completed" || expansion.status === "deadline_exceeded"
          ? expansion.status
          : "not_requested",
      relationTypes: asArray<string>(expansion.relation_types),
      depth: num(expansion.depth),
      results: asArray(expansion.results).map(retrievalResult),
    },
    appliedFilters: retrievalFilters(body.applied_filters),
    ambiguity: retrievalAmbiguity(body.ambiguity),
    operationalWarnings: asArray<string>(body.operational_warnings),
    stageTimings: Object.fromEntries(
      Object.entries(stageTimings)
        .filter(([, value]) => typeof value === "number")
        .map(([stage, value]) => [stage, value as number])
    ),
  };
};
const mapAsk = (payload: unknown): AskResponse => {
  const body = rec(payload);
  const model = rec(body.model);
  const usage = rec(model.usage);
  return {
    ...retrievalResponse(payload),
    answer: nullableString(body.answer),
    answerCitationIds: asArray<string>(body.answer_citation_ids),
    answerUnavailableReason: nullableString(body.answer_unavailable_reason),
    answerValidation: nullableString(body.answer_validation),
    answerLatencyMs: nullableInteger(body.answer_latency_ms),
    model: Object.keys(model).length
      ? {
          id: str(model.id),
          assignmentId: str(model.assignment_id, str(model.id)),
          name: str(model.name, str(model.model_name)),
          version: str(model.version, str(model.name, str(model.model_name))),
          usage: Object.keys(usage).length
            ? {
                promptTokens: num(usage.prompt_tokens),
                completionTokens: num(usage.completion_tokens),
                totalTokens: num(usage.total_tokens),
              }
            : null,
        }
      : null,
  };
};
const retrievalFilterOptions = (payload: unknown): RetrievalFilterOptions => {
  const body = rec(payload);
  const statuses = rec(body.governance_statuses);
  return {
    ontologyVersion: str(body.ontology_version),
    categories: asArray<AnyRecord>(body.categories).map((category) => ({
      id: str(category.id),
      label: str(category.label),
    })),
    entityTypes: asArray<string>(body.entity_types),
    relationshipTypes: asArray<string>(body.relationship_types),
    expansionRelationshipTypes: asArray<string>(body.expansion_relationship_types),
    ideaBlockTypes: asArray<string>(body.idea_block_types),
    ideaBlockStatuses: asArray<string>(body.idea_block_statuses),
    governanceRecordTypes: asArray<string>(body.governance_record_types),
    governanceStatuses: Object.fromEntries(
      Object.entries(statuses).map(([recordType, states]) => [recordType, asArray<string>(states)])
    ),
    architectureBacklogStates: asArray<string>(body.architecture_backlog_states),
    sensitivities: asArray<string>(body.sensitivities),
  };
};

export const assistantClient = {
  filterOptions: async (signal?: AbortSignal): Promise<RetrievalFilterOptions> =>
    retrievalFilterOptions(
      await apiClient.get<AnyRecord>("/api/retrieval/filter-options", { signal })
    ),
  search: async (request: SearchRequest, signal?: AbortSignal): Promise<RetrievalResponse> =>
    retrievalResponse(
      await apiClient.post<AnyRecord>("/api/search", retrievalBody(request), {
        signal,
        timeoutMs: ASK_TIMEOUT_MS,
      })
    ),
  ask: async (request: AskRequest, signal?: AbortSignal): Promise<AskResponse> =>
    mapAsk(
      await apiClient.post<AnyRecord>("/api/ask", retrievalBody(request), {
        signal,
        timeoutMs: ASK_TIMEOUT_MS,
      })
    ),
  evidence: async (id: string, signal?: AbortSignal) => {
    const body = await apiClient.get<{ idea_block: unknown }>(
      `/api/evidence/${encodeURIComponent(id)}`,
      { signal }
    );
    return body.idea_block ? ideaBlockSummary(body.idea_block) : null;
  },
};
