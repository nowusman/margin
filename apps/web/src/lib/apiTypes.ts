export type ApiState<T> = { data: T | null; loading: boolean; error: string | null };

export type DashboardSummary = {
  reviewTasksAssigned: number;
  staleClaims: number;
  expiredWaivers: number;
  recentAdrs: Array<{ id: string; title: string; date: string }>;
  topRisks: Array<{ id: string; title: string; severity: "high" | "medium" | "low" }>;
  topDebt: Array<{ id: string; title: string; impact: string }>;
};

export type HeatmapDomain = {
  id: string;
  name: string;
  healthScore: number;
  staleClaims: number;
  risks: number;
  debt: number;
  atdi: number;
  highSeverityRisks: number;
  expiredWaivers: number;
  unownedEntities: number;
  missingAdrCoverage: number;
  missingDrSecurityEvidence: number;
  trend: number[];
};

export type IngestionJobStageDetail = {
  stage: string;
  worker: string;
  adapter: string;
  startedAt: string;
  finishedAt: string;
  logs: string[];
  warnings: string[];
  outputCounts: Record<string, unknown>;
};

export type ReviewTask = {
  id: string;
  title: string;
  priority: "high" | "medium" | "low";
  dueDate: string;
  status: "open" | "in_review";
};

export type IngestionJob = {
  id: string;
  sourceDocumentId: string;
  sourceName: string;
  state: string;
  status: string;
  stage: string;
  attempts: number;
  errorMessage: string;
  createdAt: string;
  updatedAt: string;
  startedAt: string;
  finishedAt: string;
  qualityReport: Record<string, unknown>;
  stageDetails: IngestionJobStageDetail[];
};

export type AuditEvent = { id: string; actor: string; action: string; target: string; at: string };
export type C4Node = { id: string; level: string; name: string };
export type C4Overlay = { id: string; type: string; entity: string };

export type GovernanceRecordType =
  | "adr"
  | "risk"
  | "waiver"
  | "technical_debt"
  | "architecture_backlog";

export type GovernanceRecordRevision = {
  id: string;
  revisionNumber: number;
  snapshot: Record<string, unknown>;
  transitionReason: string;
  actor: string;
  process: string;
  createdAt: string | null;
};

export type GovernanceRecordRelation = {
  id: string;
  sourceRecordId: string;
  targetRecordId: string;
  relationType: string;
  status: string;
  validFrom: string | null;
  validTo: string | null;
  provenance: Record<string, unknown>;
};

export type GovernanceRecordEvidence = {
  id: string;
  ideaBlockId: string | null;
  ideaBlockRevisionId: string | null;
  sourceElementId: string | null;
  governanceRecordRevisionId: string | null;
  sourceQuote: string;
  polarity: string;
  status: string;
};

export type GovernanceRecordAuditEvent = {
  id: string;
  action: string;
  actor: string;
  targetType: string;
  targetId: string;
  correlationId: string | null;
  createdAt: string | null;
};

export type GovernanceRecord = {
  id: string;
  businessKey: string;
  recordType: GovernanceRecordType;
  title: string;
  domainId: string;
  lifecycleStatus: string;
  ownerId: string | null;
  stewardId: string | null;
  effectiveDate: string | null;
  reviewDate: string | null;
  expiryDate: string | null;
  targetDate: string | null;
  priority: string | null;
  severity: number | null;
  externalIssueId: string | null;
  externalIssueUrl: string | null;
  schemaVersion: string;
  attributes: Record<string, unknown>;
  createdAt: string | null;
  updatedAt: string | null;
  currentRevision: GovernanceRecordRevision | null;
  history: GovernanceRecordRevision[];
  ideaBlocks: Array<{ id: string; role: string; displayOrder: number; domainId: string | null }>;
  entities: Array<{ id: string; role: string }>;
  relations: GovernanceRecordRelation[];
  audit: GovernanceRecordAuditEvent[];
  evidence: GovernanceRecordEvidence[];
};

export type GovernanceRecordCreateInput = {
  businessKey: string;
  recordType: GovernanceRecordType;
  title: string;
  domainId: string;
  attributes: Record<string, unknown>;
  ownerId?: string | null;
  stewardId?: string | null;
  effectiveDate?: string | null;
  reviewDate?: string | null;
  expiryDate?: string | null;
  targetDate?: string | null;
  priority?: string | null;
  severity?: number | null;
  externalIssueId?: string | null;
  externalIssueUrl?: string | null;
};

export type GovernanceRecordUpdateInput = Omit<
  GovernanceRecordCreateInput,
  "businessKey" | "recordType" | "domainId"
> & {
  reason: string;
  expectedRevisionId: string;
};

export type RelationshipEntity = {
  id: string;
  label: string;
  ontologyType: string;
  domainId: string;
  lifecycleStatus: string;
};

export type ExplicitRelationshipProvenance = {
  id: string;
  ideaBlockId: string;
  sourceElementId: string;
  sourceQuote: string | null;
  polarity: string;
  status: string;
  validFrom: string | null;
  validTo: string | null;
};

export type ExplicitRelationshipEdge = {
  relationshipId: string;
  source: RelationshipEntity;
  target: RelationshipEntity;
  relationType: string;
  confidence: string;
  status: string;
  validFrom: string | null;
  validTo: string | null;
  direction: string;
  hop: number;
  provenance: ExplicitRelationshipProvenance[];
};

export type RelationshipNeighborResult = {
  entityId: string;
  edges: ExplicitRelationshipEdge[];
  capped: boolean;
};

export type RelationshipPathResult = {
  sourceEntityId: string;
  targetEntityId: string;
  path: ExplicitRelationshipEdge[];
  found: boolean;
  capped: boolean;
};

export type RelationshipImpactRecord = {
  entity: RelationshipEntity;
  hop: number;
  relationType: string | null;
  path: ExplicitRelationshipEdge[];
};

export type RelationshipImpactResult = {
  entityId: string;
  impacts: RelationshipImpactRecord[];
  capped: boolean;
};

export type GovernedEntityRelationship = {
  governanceRecordId: string;
  businessKey: string;
  title: string;
  recordType: string;
  lifecycleStatus: string;
  domainId: string;
  role: string;
};

export type GovernedEntityRelationshipResult = {
  relationships: GovernedEntityRelationship[];
  capped: boolean;
};

export type IdeaBlockRelationship = {
  relationshipId: string;
  source: { id: string; statement: string; type: string; status: string; domainId: string };
  target: { id: string; statement: string; type: string; status: string; domainId: string };
  relationType: string;
  direction: string;
  status: string;
  confidence: number;
  validFrom: string | null;
  validTo: string | null;
  decisionProvenance: Record<string, unknown>;
};

export type IdeaBlockRelationshipResult = {
  ideaBlockId: string;
  relations: IdeaBlockRelationship[];
  capped: boolean;
};

export type ReviewQueueItem = {
  id: string;
  type: string;
  title: string;
  priority: string;
  status: string;
  domain: string;
  evidenceId: string;
  proposedChange: string;
  comments: string[];
  updatedAt?: string | null;
};

export type SemanticReviewAction =
  | "confirm_equivalent"
  | "approve_distinct"
  | "mark_refinement"
  | "mark_contradiction"
  | "mark_supersession";

export type ReviewEvidence = {
  id: string;
  source_quote: string;
  source_element_id: string;
  evidence_kind: string;
  polarity: string;
  status: string;
  valid_from: string | null;
  valid_to: string | null;
  source_version: {
    id: string;
    status: string;
    source_document_id: string;
    version_sha256: string;
    superseded_by_version_id: string | null;
  } | null;
  context: {
    section_heading?: string;
    section_path?: string;
    before: string | null;
    current: string | null;
    after: string | null;
  };
};

export type ReviewBundle = {
  task: {
    id: string;
    status: string;
    type: string;
    reason: string;
    updated_at: string | null;
    target_type: string;
    target_id: string;
  };
  claims: Array<{
    id: string;
    statement: string;
    type: string;
    confidence: string;
    status: string;
    revision_id: string;
    evidence: ReviewEvidence[];
  }>;
  current_statements: Array<{ idea_block_id: string; revision: ReviewRevision }>;
  revisions: ReviewRevision[];
  evidence: ReviewEvidence[];
  entities: Array<{
    idea_block_id: string;
    id: string;
    name: string;
    kind: string;
    role: string;
    status: string;
  }>;
  entity_resolutions: Array<{
    id: string;
    mention_id: string;
    mention: string;
    entity_id: string;
    outcome: string;
    confidence: number;
    candidate_entity_ids: string[];
    candidates: Array<{ id: string; name: string; kind: string }>;
    status: string;
  }>;
  relationships: Array<{
    id: string;
    source_entity_id: string;
    target_entity_id: string;
    relation_type: string;
    status: string;
    evidence: Array<Record<string, unknown>>;
  }>;
  idea_block_relations: Array<{
    id: string;
    source_idea_block_id: string;
    target_idea_block_id: string;
    relation_type: string;
    direction: string;
    status: string;
    confidence: number;
    valid_from: string | null;
    valid_to: string | null;
    decision_provenance: Record<string, unknown>;
  }>;
  governance_records: Array<{
    id: string;
    business_key: string;
    record_type: string;
    title: string;
    domain_id: string;
    lifecycle_status: string;
  }>;
  contradictions: string[];
  protected_field_differences: Record<string, { left: unknown; right: unknown }>;
  suggested_outcome: string | null;
  decision_provenance: Record<string, unknown>;
  semantic_resolution?: Record<string, unknown> | null;
  unmapped_extractions: Array<Record<string, unknown>>;
};

export type ReviewRevision = {
  id: string;
  idea_block_id: string;
  revision_number: number;
  statement: string;
  normalized_statement: string;
  type: string;
  confidence: string;
  polarity: string;
  qualifiers: Record<string, unknown>;
  valid_from: string | null;
  valid_to: string | null;
  publication_status: string;
  semantic_key: string;
  actor: string;
  process: string;
  change_reason: string | null;
  metadata: Record<string, unknown>;
  created_at: string | null;
};

export type SourceSummary = {
  id: string;
  title: string;
  originalFilename: string;
  sourceUri: string;
  mediaType: string;
  domainId: string;
  category: string;
  sensitivity: string;
  status: string;
  owner: string;
  steward: string;
  createdAt: string;
  pages?: SourcePage[];
  elements?: SourceElement[];
};

export type SourcePage = {
  id: string;
  sourceVersionId: string;
  pageNumber: number;
  width?: number | null;
  height?: number | null;
  dpi?: number | null;
  assetKey?: string | null;
  checksum?: string | null;
};

export type SourceElement = {
  id: string;
  sourceVersionId: string;
  sourcePageId?: string | null;
  parentElementId?: string | null;
  nativeElementId?: string | null;
  kind: string;
  elementType?: string;
  ordinal: number;
  text: string;
  checksum: string;
  pageNumber?: number | null;
  charStart?: number | null;
  charEnd?: number | null;
  boundingBox?: unknown;
  assetKey?: string | null;
  converterMethod?: string | null;
  converterVersion?: string | null;
  confidence?: number | null;
};

export type SourceVersion = {
  id: string;
  versionHash: string;
  createdAt: string;
  converterName: string;
  converterVersion: string;
  status: string;
  supersededByVersionId: string | null;
};

export type ReprocessingDiff = {
  id: string;
  previousVersionId: string | null;
  currentVersionId: string;
  extractionRunId: string | null;
  addedClaimIds: unknown[];
  removedClaimIds: unknown[];
  modifiedClaims: unknown[];
  changedEntityResolutions: unknown[];
  addedRelationshipIds: unknown[];
  removedRelationshipIds: unknown[];
  newlyUnresolved: unknown[];
  createdAt: string;
};

export type SourceHistory = {
  versions: SourceVersion[];
  pages: SourcePage[];
  reprocessingDiffs: ReprocessingDiff[];
};

export type DomainSummary = {
  id: string;
  name: string;
  owner: string;
  steward: string;
  status: string;
  description: string;
};

export type ExtractionReport = {
  sourceDocumentId: string;
  report: Record<string, unknown>;
};

export type IngestionReceipt = {
  source_document_id: string;
  source_version_id: string;
  source_status: string;
  conversion: Record<string, unknown>;
  index: Record<string, unknown>;
  operational: {
    run_id: string | null;
    status: string;
    expected_windows: number;
    processed_windows: number;
    mode: string | null;
    attempted_elements: number;
    no_claim_elements: number;
    failed_elements: number;
    deferred_elements: number;
    unsupported_elements: number;
    outcomes: Record<string, number>;
    input_tokens: number;
    output_tokens: number;
    warnings: string[];
    terminal_window_coverage: boolean;
  };
  knowledge: Record<string, unknown>;
  observability: {
    native_elements: number;
    mapped_elements: number;
    unsupported_elements: number;
    previous_unsupported_elements: number;
    unsupported_element_growth: number;
    element_outcomes: Record<string, number>;
    attempt_outcomes: Record<string, number>;
    exact_claims: number;
    quarantined_facts: number;
    no_claim_audit_results: {
      selected: number;
      passed: number;
      pending: number;
      not_selected: number;
    };
    candidate_entity_promotions: number;
    rejected_or_tombstoned_candidates: number;
    semantic_decisions: {
      equivalent: number;
      distinct: number;
      refinement: number;
      contradiction: number;
      supersession: number;
      review_tasks: number;
    };
    embedding_gate: Record<string, unknown>;
  };
  unresolved_elements: SourceElementIssue[];
  completeness: Record<
    "operational" | "source" | "extraction" | "publication",
    {
      status: "complete" | "requires_review" | "failed";
      [key: string]: unknown;
    }
  >;
  overall_status: "complete" | "requires_review" | "failed";
};

export type SourceElementIssue = {
  id: string;
  kind: string;
  page: number | null;
  reason: string | null;
  disposition: string;
  outcome: string;
  attempt: { id: string; outcome: string; attempt_count: number } | null;
  review_task_id: string | null;
};

export type EntitySummary = {
  id: string;
  name: string;
  kind: string;
  category: string;
  domainId: string;
  owner: string;
  steward: string;
  status: string;
  description: string;
  metadata: Record<string, unknown>;
};

export type IdeaBlockSummary = {
  id: string;
  domainId: string;
  currentRevisionId: string;
  statement: string;
  category: string;
  type: string;
  confidence: string;
  status: string;
  revisionNumber: number;
  publicationStatus: string;
  revisions: Array<{
    id: string;
    revisionNumber: number;
    statement: string;
    normalizedStatement: string;
    type: string;
    confidence: string;
    polarity: string;
    qualifiers: Record<string, unknown>;
    validFrom: string | null;
    validTo: string | null;
    publicationStatus: string;
    ontologyVersion: string;
    actor: string;
    process: string;
    changeReason: string | null;
    createdAt: string | null;
  }>;
  evidence: Array<{
    id: string;
    ideaBlockRevisionId: string;
    sourcePageId: string | null;
    sourceElementId: string;
    sourceQuote: string;
    exactQuoteStart: number | null;
    exactQuoteEnd: number | null;
    evidenceKind: string;
    polarity: string;
    status: string;
    redacted: boolean;
    redactionReason: string | null;
  }>;
  relations: Array<{
    id: string;
    sourceIdeaBlockId: string;
    targetIdeaBlockId: string;
    relationType: string;
    direction: "directed" | "symmetric";
    status: string;
    validFrom: string | null;
    validTo: string | null;
    confidence: number;
    decisionProvenance: Record<string, unknown>;
  }>;
  metadata: Record<string, unknown>;
};

export type ManualIdeaBlockEntityInput = {
  name: string;
  kind: string;
  role: string;
  externalId?: string;
};

export type ManualIdeaBlockRelationshipInput = {
  source: string;
  relationType: string;
  target: string;
  confidence: string;
};

export type ManualIdeaBlockInput = {
  statement: string;
  domainId: string;
  category: string;
  type: string;
  confidence: string;
  sourceQuote: string;
  entities: ManualIdeaBlockEntityInput[];
  relationships: ManualIdeaBlockRelationshipInput[];
};

export type DiagramSummary = {
  id: string;
  type: "structurizr" | "mermaid" | "plantuml";
  title: string;
  markup?: string;
  svg?: string;
  evidence?: Array<Record<string, unknown>>;
};

export type EvaluationRun = {
  id: string;
  status: string;
  corpusVersion: string;
  evaluationMode: string;
  startedAt: string;
  completedAt: string;
  activeExtractionModelId: string;
  metrics: Record<string, unknown>;
  gateFailures: string[];
  fixtureCount: number;
  falsePositives: EvaluationIssue[];
  falseNegatives: EvaluationIssue[];
  metricWarning: string;
};

export type EvaluationIssue = {
  fixtureId: string;
  expectedId: string;
  actualIds: string[];
  evidenceIds: string[];
  sourceLinks: Array<{
    sourceDocumentId: string;
    sourceElementId: string | null;
    quote: string | null;
    page?: number | null;
    url: string;
  }>;
};

export type RetrievalFilters = {
  domainIds: string[];
  categoryIds: string[];
  entityIds: string[];
  entityTerms: string[];
  entityTypes: string[];
  relationshipTypes: string[];
  ideaBlockTypes: string[];
  sourceDocumentIds: string[];
  sensitivities: string[];
  statuses: string[];
  validFrom: string | null;
  validTo: string | null;
  governanceRecordTypes: string[];
  governanceStatuses: string[];
  architectureBacklogStates: string[];
};

export type RetrievalExpansion = {
  relationTypes: string[];
  depth: number;
  limit: number;
};

export type SearchRequest = {
  query: string;
  outputLanguage?: string;
  filters: RetrievalFilters;
  topK: number;
  expand: RetrievalExpansion;
  contextBudgetChars?: number | null;
  threadId?: string | null;
};

export type AskRequest = SearchRequest;

export type RetrievalCitation = {
  id: string;
  ideaBlockId: string;
  ideaBlockRevisionId: string;
  sourceDocumentId: string;
  sourceTitle: string;
  sourceVersionId: string;
  sourceVersionSha256: string;
  sourcePageId: string;
  sourceElementId: string;
  pageNumber: number | null;
  boundingBox: Record<string, unknown> | null;
  quote: string;
  quoteCharStart: number | null;
  quoteCharEnd: number | null;
  elementType: string;
  polarity: string;
  validFrom: string | null;
  validTo: string | null;
  governanceRecordId: string;
  governanceRecordRevisionId: string;
  governanceRecordType: string;
  assetKey: string;
  redacted: boolean;
  redactionReason: string;
  entityId: string;
  entityName: string;
  confidence: string;
};

export type RetrievalResultCard = {
  ideaBlockId: string;
  ideaBlockRevisionId: string;
  score: number;
  statement: string;
  snippet: string;
  type: string;
  status: string;
  polarity: string;
  validFrom: string | null;
  validTo: string | null;
  entities: Array<{ id: string; name: string; type: string }>;
  citations: RetrievalCitation[];
  contradictions: Array<{ id: string; relationType: string; status: string }>;
  supersession: Array<{ id: string; relationType: string; status: string }>;
  governanceRecords: Array<{ id: string; type: string; status: string; title: string }>;
  related: Array<{ kind: string; id: string; label: string; type: string }>;
};

export type RetrievalFusion = {
  mode: string;
  channels: Record<string, number>;
  count: number;
};

export type RetrievalAmbiguity = {
  entityTerms: Array<{ term: string; candidateEntityIds: string[]; candidateTypes: string[] }>;
};

export type RetrievalResponse = {
  contractVersion: "v1";
  responseMode: "hybrid" | "lexical_only" | "search_only";
  results: RetrievalResultCard[];
  fusion: RetrievalFusion;
  expansion: {
    status: "not_requested" | "completed" | "deadline_exceeded";
    relationTypes: string[];
    depth: number;
    results: RetrievalResultCard[];
  };
  appliedFilters: RetrievalFilters;
  ambiguity: RetrievalAmbiguity;
  operationalWarnings: string[];
  stageTimings: Record<string, number>;
};

export type RetrievalFilterOptions = {
  ontologyVersion: string;
  categories: Array<{ id: string; label: string }>;
  entityTypes: string[];
  relationshipTypes: string[];
  expansionRelationshipTypes: string[];
  ideaBlockTypes: string[];
  ideaBlockStatuses: string[];
  governanceRecordTypes: string[];
  governanceStatuses: Record<string, string[]>;
  architectureBacklogStates: string[];
  sensitivities: string[];
};

export type AskResponse = RetrievalResponse & {
  answer: string | null;
  answerCitationIds: string[];
  answerUnavailableReason: string | null;
  answerValidation: string | null;
  answerLatencyMs: number | null;
  model: {
    id: string;
    assignmentId: string;
    name: string;
    version: string;
    usage: { promptTokens: number; completionTokens: number; totalTokens: number } | null;
  } | null;
};

export type ModelRegistryItem = {
  id: string;
  displayName: string;
  purpose: "extraction" | "vision_ocr" | "answer_synthesis" | "semantic_resolution";
  runtime: string;
  modelName: string;
  endpointUrl: string;
  secretConfigured: boolean;
  capabilities: Record<string, unknown>;
  contextLength: number;
  maxTokens: number;
  temperatureDefault: number;
  license: string;
  hardwareProfile: string;
  status: "active" | "disabled" | "experimental";
  defaultScopes: string[];
  systemPrompt: string;
  lastTestStatus: string | null;
  lastTestLatencyMs: number | null;
  lastTestDetail: string | null;
  lastTestedAt: string | null;
};

export type GraphNode = {
  id: string;
  label: string;
  kind: string;
  domainId: string;
  definition: string;
  degree: number;
  communityId: string;
  ontologyVersion: string;
};

export type GraphEdge = {
  id: string;
  sourceId: string;
  targetId: string;
  relationType: string;
  confidence: string;
  status: string;
  validFrom: string;
  validTo: string;
  quotes: string[];
  ideaBlockIds: string[];
};

export type GraphCommunity = {
  communityId: string;
  memberIds: string[];
  internalEdgeCount: number;
  boundaryEdgeCount: number;
  cohesion: number;
  meanIntraDegree: number;
};

export type GraphSubgraph = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  communities: GraphCommunity[];
  capped: boolean;
  depthReached: number;
  ontologyVersion: string;
  scope: { entityCount: number; rootEntityId: string; depth: number };
};

export type GraphSurprisingConnection = {
  edgeId: string;
  sourceId: string;
  targetId: string;
  score: number;
  reasons: string[];
};

export type GraphAffectedRecord = {
  id: string;
  label: string;
  kind: string;
};

export type GraphKnowledgeGap = {
  gapKey: string;
  gapType: string;
  title: string;
  description: string;
  nodeIds: string[];
  suggestion: string;
  researchTerm: string;
  researchContext: string;
  affectedRecords: GraphAffectedRecord[];
};

export type GraphInsights = {
  surprisingConnections: GraphSurprisingConnection[];
  knowledgeGaps: GraphKnowledgeGap[];
  hiddenCount: number;
  ontologyVersion: string;
};

export type DomainCharter = {
  id: string;
  domainId: string;
  version: number;
  status: "active" | "superseded" | "draft";
  authorPrincipalId: string;
  purpose: string;
  scopeInclusions: string;
  scopeExclusions: string;
  terminology: string;
  createdAt: string;
  activatedAt: string;
  supersededAt: string;
};

export type DomainCharterView = {
  domainId: string;
  active: DomainCharter | null;
  history: DomainCharter[];
};

export type DomainBriefing = {
  domainId: string;
  briefing: string;
  truncated: boolean;
  charterVersion: number | null;
  entityCount: number;
  ideaBlockCount: number;
  governanceCounts: Record<string, number>;
};

export type NarrativeReference = {
  kind: string;
  id: string;
  label: string;
};

export type DomainNarrative = {
  domainId: string;
  title: string;
  markdown: string;
  truncated: boolean;
  charterVersion: number | null;
  entityCount: number;
  claimCount: number;
  inferredClaimCount: number;
  decisionCount: number;
  references: NarrativeReference[];
};

export type KnownUnknown = {
  placeholderId: string;
  domainId: string;
  text: string;
  normalizedText: string;
  proposedType: string;
  referenceCount: number;
  mentionIds: string[];
};

export type PersonalNote = {
  id: string;
  targetType: string;
  targetId: string;
  targetLabel: string;
  targetKind: string;
  provenance: string[];
  body: string;
  visibility: string;
  domainId: string;
  createdAt: string;
  updatedAt: string;
};

export type SavedItem = {
  id: string;
  targetType: string;
  targetId: string;
  targetLabel: string;
  targetKind: string;
  provenance: string[];
  label: string;
  tags: string[];
  domainId: string;
  createdAt: string;
};

export type PersonalFeedEntry = {
  targetType: string;
  targetId: string;
  targetLabel: string;
  targetKind: string;
  provenance: string[];
  reason: string;
  viewedAt: string;
  revisionId: string;
};

export type PersonalFeed = {
  feed: PersonalFeedEntry[];
  queue: PersonalFeedEntry[];
  permittedDomains: string[];
};

export type AssistantThreadSummary = {
  id: string;
  title: string;
  domainId: string;
  createdAt: string;
  updatedAt: string;
};

export type AssistantMessage = {
  id: string;
  role: string;
  content: string;
  citationIds: string[];
  position: number;
  createdAt: string;
};

export type AssistantThread = AssistantThreadSummary & {
  messages: AssistantMessage[];
};

export type AgentRunSummary = {
  id: string;
  principalId: string;
  domainId: string;
  question: string;
  state: string;
  iterationBudget: number;
  tokenBudget: number;
  iterationsUsed: number;
  tokensUsed: number;
  writeGranted: boolean;
  answer: string;
  error: string;
  createdAt: string;
  completedAt: string;
};

export type AgentEvent = {
  id: string;
  sequence: number;
  kind: string;
  payload: Record<string, unknown>;
  createdAt: string;
};

export type AgentProposal = {
  id: string;
  runId: string;
  targetType: string;
  targetId: string;
  path: string;
  summary: string;
  diff: string;
  state: string;
  reviewedBy: string;
  reviewedAt: string;
  createdAt: string;
};

export type AgentQuestion = {
  id: string;
  requestKey: string;
  title: string;
  description: string;
  fields: { key: string; label: string; type: string; options: string[]; default: string }[];
  state: string;
  response: Record<string, unknown>;
};

export type AgentRunDetail = AgentRunSummary & {
  events: AgentEvent[];
  proposals: AgentProposal[];
  questions: AgentQuestion[];
};

export type AgentPlaybook = {
  name: string;
  description: string;
  version: string;
  domainId: string;
  appliesTo: string[];
  path: string;
  supportingFiles: string[];
};

export type DomainExport = {
  exportId: string;
  domainId: string;
  state: string;
  contentHash: string;
  manifest: {
    format_version: string;
    domain_id: string;
    generated_at: string;
    content_hash: string;
    record_kinds: Record<string, number>;
    included_record_kinds: string[];
    excluded_by_construction: string[];
  };
  error: string;
  createdAt: string;
  completedAt: string;
};
