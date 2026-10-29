import { apiClient } from "./api-client";
import { AnyRecord, asArray, num, rec, str } from "./apiMappers";
import type {
  GovernanceRecord,
  GovernanceRecordAuditEvent,
  GovernanceRecordCreateInput,
  GovernanceRecordEvidence,
  GovernanceRecordRelation,
  GovernanceRecordRevision,
  GovernanceRecordType,
  GovernanceRecordUpdateInput,
  ReviewBundle,
  ReviewQueueItem,
  SemanticReviewAction,
} from "./apiTypes";

const nullableNumber = (value: unknown): number | null =>
  typeof value === "number" ? value : null;
const nullableString = (value: unknown): string | null =>
  typeof value === "string" ? value : null;

const reviewTask = (value: unknown): ReviewQueueItem => {
  const item = rec(value);
  const evidence = rec(asArray<unknown>(item.evidence)[0]);
  return {
    id: str(item.id),
    type: str(item.task_type, "review"),
    title: str(item.reason, str(item.target_id, "Review task")),
    priority: str(item.priority, "medium"),
    status: str(item.status, "open"),
    domain: str(item.domain_id, "Unassigned"),
    evidenceId: str(evidence.idea_block_id, str(item.target_id)),
    proposedChange: str(item.recommendation, str(item.resolution, "Review evidence and decide")),
    comments: asArray<AnyRecord>(item.comments).map((comment) => str(comment.comment)),
    updatedAt: item.updated_at ? str(item.updated_at) : null,
  };
};

const governanceRecordRevision = (value: unknown): GovernanceRecordRevision | null => {
  if (!value || typeof value !== "object") return null;
  const item = rec(value);
  return {
    id: str(item.id),
    revisionNumber: num(item.revision_number),
    snapshot: rec(item.snapshot),
    transitionReason: str(item.transition_reason),
    actor: str(item.actor),
    process: str(item.process),
    createdAt: nullableString(item.created_at),
  };
};

const governanceRecordRelation = (value: unknown): GovernanceRecordRelation => {
  const item = rec(value);
  return {
    id: str(item.id),
    sourceRecordId: str(item.source_record_id),
    targetRecordId: str(item.target_record_id),
    relationType: str(item.relation_type),
    status: str(item.status),
    validFrom: nullableString(item.valid_from),
    validTo: nullableString(item.valid_to),
    provenance: rec(item.provenance),
  };
};

const governanceRecordEvidence = (value: unknown): GovernanceRecordEvidence => {
  const item = rec(value);
  return {
    id: str(item.id),
    ideaBlockId: nullableString(item.idea_block_id),
    ideaBlockRevisionId: nullableString(item.idea_block_revision_id),
    sourceElementId: nullableString(item.source_element_id),
    governanceRecordRevisionId: nullableString(item.governance_record_revision_id),
    sourceQuote: str(item.source_quote),
    polarity: str(item.polarity, "supporting"),
    status: str(item.status, "candidate"),
  };
};

const governanceRecordAuditEvent = (value: unknown): GovernanceRecordAuditEvent => {
  const item = rec(value);
  return {
    id: str(item.id),
    action: str(item.action),
    actor: str(item.actor),
    targetType: str(item.target_type),
    targetId: str(item.target_id),
    correlationId: nullableString(item.correlation_id),
    createdAt: nullableString(item.created_at),
  };
};

const governanceRecord = (value: unknown): GovernanceRecord => {
  const item = rec(value);
  const currentRevision = governanceRecordRevision(item.current_revision);
  return {
    id: str(item.id),
    businessKey: str(item.business_key),
    recordType: str(item.record_type) as GovernanceRecordType,
    title: str(item.title),
    domainId: str(item.domain_id),
    lifecycleStatus: str(item.lifecycle_status),
    ownerId: nullableString(item.owner_id),
    stewardId: nullableString(item.steward_id),
    effectiveDate: nullableString(item.effective_date),
    reviewDate: nullableString(item.review_date),
    expiryDate: nullableString(item.expiry_date),
    targetDate: nullableString(item.target_date),
    priority: nullableString(item.priority),
    severity: nullableNumber(item.severity),
    externalIssueId: nullableString(item.external_issue_id),
    externalIssueUrl: nullableString(item.external_issue_url),
    schemaVersion: str(item.schema_version),
    attributes: rec(item.attributes),
    createdAt: nullableString(item.created_at),
    updatedAt: nullableString(item.updated_at),
    currentRevision,
    history: asArray(item.history)
      .map(governanceRecordRevision)
      .filter((revision): revision is GovernanceRecordRevision => revision !== null),
    ideaBlocks: asArray<AnyRecord>(item.idea_blocks).map((link) => ({
      id: str(link.id),
      role: str(link.role),
      displayOrder: num(link.display_order),
      domainId: nullableString(link.domain_id),
    })),
    entities: asArray<AnyRecord>(item.entities).map((link) => ({
      id: str(link.id),
      role: str(link.role),
    })),
    relations: asArray(item.relations).map(governanceRecordRelation),
    audit: asArray(item.audit).map(governanceRecordAuditEvent),
    evidence: asArray(item.evidence).map(governanceRecordEvidence),
  };
};

const governanceRecordBody = (input: GovernanceRecordCreateInput): AnyRecord => ({
  business_key: input.businessKey,
  record_type: input.recordType,
  title: input.title,
  domain_id: input.domainId,
  attributes: input.attributes,
  owner_id: input.ownerId ?? null,
  steward_id: input.stewardId ?? null,
  effective_date: input.effectiveDate ?? null,
  review_date: input.reviewDate ?? null,
  expiry_date: input.expiryDate ?? null,
  target_date: input.targetDate ?? null,
  priority: input.priority ?? null,
  severity: input.severity ?? null,
  external_issue_id: input.externalIssueId ?? null,
  external_issue_url: input.externalIssueUrl ?? null,
});

const governanceRecordUpdateBody = (
  input: Omit<GovernanceRecordUpdateInput, "reason" | "expectedRevisionId">
): AnyRecord => ({
  title: input.title,
  attributes: input.attributes,
  owner_id: input.ownerId ?? null,
  steward_id: input.stewardId ?? null,
  effective_date: input.effectiveDate ?? null,
  review_date: input.reviewDate ?? null,
  expiry_date: input.expiryDate ?? null,
  target_date: input.targetDate ?? null,
  priority: input.priority ?? null,
  severity: input.severity ?? null,
  external_issue_id: input.externalIssueId ?? null,
  external_issue_url: input.externalIssueUrl ?? null,
});

const recordEndpoint = (recordId: string, suffix = ""): string =>
  `/api/governance/records/${encodeURIComponent(recordId)}${suffix}`;

const recordResponse = async (request: Promise<{ record: unknown }>): Promise<GovernanceRecord> =>
  governanceRecord((await request).record);

export const governanceClient = {
  records: async (
    filters: {
      recordType?: GovernanceRecordType;
      lifecycleStatus?: string;
      ownerId?: string;
      domainId?: string;
      externalIssueId?: string;
    } = {}
  ): Promise<GovernanceRecord[]> => {
    const params = new URLSearchParams();
    if (filters.recordType) params.set("record_type", filters.recordType);
    if (filters.lifecycleStatus) params.set("lifecycle_status", filters.lifecycleStatus);
    if (filters.ownerId) params.set("owner_id", filters.ownerId);
    if (filters.domainId) params.set("domain_id", filters.domainId);
    if (filters.externalIssueId) params.set("external_issue_id", filters.externalIssueId);
    const query = params.toString();
    const body = await apiClient.get<{ records: unknown[] }>(
      `/api/governance/records${query ? `?${query}` : ""}`
    );
    return asArray(body.records).map(governanceRecord);
  },
  record: (recordId: string): Promise<GovernanceRecord> =>
    recordResponse(apiClient.get<{ record: unknown }>(recordEndpoint(recordId))),
  createRecord: (input: GovernanceRecordCreateInput): Promise<GovernanceRecord> =>
    recordResponse(
      apiClient.post<{ record: unknown }>("/api/governance/records", governanceRecordBody(input))
    ),
  updateRecord: (
    recordId: string,
    input: GovernanceRecordUpdateInput
  ): Promise<GovernanceRecord> => {
    const { reason, expectedRevisionId, ...changes } = input;
    return recordResponse(
      apiClient.patch<{ record: unknown }>(recordEndpoint(recordId), {
        ...governanceRecordUpdateBody(changes),
        reason,
        expected_revision_id: expectedRevisionId,
      })
    );
  },
  transitionRecord: (
    recordId: string,
    input: {
      targetStatus: string;
      reason: string;
      expectedRevisionId: string;
      attributes?: Record<string, unknown>;
    }
  ): Promise<GovernanceRecord> =>
    recordResponse(
      apiClient.post<{ record: unknown }>(recordEndpoint(recordId, "/transitions"), {
        target_status: input.targetStatus,
        reason: input.reason,
        expected_revision_id: input.expectedRevisionId,
        attributes: input.attributes,
      })
    ),
  renewWaiver: (
    recordId: string,
    input: { expiryDate: string; reason: string; expectedRevisionId: string }
  ): Promise<GovernanceRecord> =>
    recordResponse(
      apiClient.post<{ record: unknown }>(recordEndpoint(recordId, "/waiver-renewals"), {
        expiry_date: input.expiryDate,
        reason: input.reason,
        expected_revision_id: input.expectedRevisionId,
      })
    ),
  revokeWaiver: (
    recordId: string,
    input: { reason: string; expectedRevisionId: string }
  ): Promise<GovernanceRecord> =>
    recordResponse(
      apiClient.post<{ record: unknown }>(recordEndpoint(recordId, "/waiver-revocations"), {
        reason: input.reason,
        expected_revision_id: input.expectedRevisionId,
      })
    ),
  acceptRisk: (
    recordId: string,
    input: { reason: string; expectedRevisionId: string }
  ): Promise<GovernanceRecord> =>
    recordResponse(
      apiClient.post<{ record: unknown }>(recordEndpoint(recordId, "/risk-acceptances"), {
        reason: input.reason,
        expected_revision_id: input.expectedRevisionId,
      })
    ),
  closeRisk: (
    recordId: string,
    input: { reason: string; expectedRevisionId: string }
  ): Promise<GovernanceRecord> =>
    recordResponse(
      apiClient.post<{ record: unknown }>(recordEndpoint(recordId, "/risk-closures"), {
        reason: input.reason,
        expected_revision_id: input.expectedRevisionId,
      })
    ),
  resolveTechnicalDebt: (
    recordId: string,
    input: { resolutionNote: string; reason: string; expectedRevisionId: string }
  ): Promise<GovernanceRecord> =>
    recordResponse(
      apiClient.post<{ record: unknown }>(recordEndpoint(recordId, "/technical-debt-resolutions"), {
        resolution_note: input.resolutionNote,
        reason: input.reason,
        expected_revision_id: input.expectedRevisionId,
      })
    ),
  completeArchitectureBacklog: (
    recordId: string,
    input: { completionNote: string; reason: string; expectedRevisionId: string }
  ): Promise<GovernanceRecord> =>
    recordResponse(
      apiClient.post<{ record: unknown }>(
        recordEndpoint(recordId, "/architecture-backlog-completions"),
        {
          completion_note: input.completionNote,
          reason: input.reason,
          expected_revision_id: input.expectedRevisionId,
        }
      )
    ),
  supersedeRecord: (
    recordId: string,
    input: {
      targetRecordId: string;
      reason: string;
      expectedRevisionId: string;
      expectedTargetRevisionId: string;
    }
  ): Promise<GovernanceRecord> =>
    recordResponse(
      apiClient.post<{ record: unknown }>(recordEndpoint(recordId, "/supersede"), {
        target_record_id: input.targetRecordId,
        reason: input.reason,
        expected_revision_id: input.expectedRevisionId,
        expected_target_revision_id: input.expectedTargetRevisionId,
      })
    ),
  addRelation: async (
    recordId: string,
    input: {
      targetRecordId: string;
      relationType: string;
      reason: string;
      expectedRevisionId: string;
      expectedTargetRevisionId: string;
      status: string;
      validFrom: string | null;
      validTo: string | null;
    }
  ): Promise<{ relationId: string }> => {
    const body = await apiClient.post<{ relation_id: string }>(
      recordEndpoint(recordId, "/relations"),
      {
        target_record_id: input.targetRecordId,
        relation_type: input.relationType,
        status: input.status,
        valid_from: input.validFrom || null,
        valid_to: input.validTo || null,
        reason: input.reason,
        expected_revision_id: input.expectedRevisionId,
        expected_target_revision_id: input.expectedTargetRevisionId,
      }
    );
    return { relationId: body.relation_id };
  },
  linkIdeaBlock: async (
    recordId: string,
    input: {
      ideaBlockId: string;
      role: string;
      reason: string;
      expectedRevisionId: string;
      displayOrder?: number;
    }
  ): Promise<void> => {
    await apiClient.post(recordEndpoint(recordId, "/idea-blocks"), {
      idea_block_id: input.ideaBlockId,
      role: input.role,
      display_order: input.displayOrder ?? 0,
      reason: input.reason,
      expected_revision_id: input.expectedRevisionId,
    });
  },
  unlinkIdeaBlock: async (
    recordId: string,
    ideaBlockId: string,
    role: string,
    reason: string,
    expectedRevisionId: string
  ): Promise<void> => {
    const query = new URLSearchParams({
      role,
      reason,
      expected_revision_id: expectedRevisionId,
    }).toString();
    await apiClient.delete(
      `${recordEndpoint(recordId, `/idea-blocks/${encodeURIComponent(ideaBlockId)}`)}?${query}`
    );
  },
  linkEntity: async (
    recordId: string,
    input: { entityId: string; role: string; reason: string; expectedRevisionId: string }
  ): Promise<void> => {
    await apiClient.post(recordEndpoint(recordId, "/entities"), {
      entity_id: input.entityId,
      role: input.role,
      reason: input.reason,
      expected_revision_id: input.expectedRevisionId,
    });
  },
  unlinkEntity: async (
    recordId: string,
    entityId: string,
    role: string,
    reason: string,
    expectedRevisionId: string
  ): Promise<void> => {
    const query = new URLSearchParams({
      role,
      reason,
      expected_revision_id: expectedRevisionId,
    }).toString();
    await apiClient.delete(
      `${recordEndpoint(recordId, `/entities/${encodeURIComponent(entityId)}`)}?${query}`
    );
  },
  createArchitectureBacklog: (
    recordId: string,
    input: {
      businessKey: string;
      title: string;
      pendingCorrection: string;
      reason: string;
      priority: string;
      targetDate: string;
      expectedOriginRevisionId: string;
    }
  ): Promise<GovernanceRecord> =>
    recordResponse(
      apiClient.post<{ record: unknown }>(recordEndpoint(recordId, "/architecture-backlog"), {
        business_key: input.businessKey,
        title: input.title,
        pending_correction: input.pendingCorrection,
        reason: input.reason,
        priority: input.priority,
        target_date: input.targetDate,
        expected_origin_revision_id: input.expectedOriginRevisionId,
      })
    ),
  reviewTasks: async (
    filters: {
      assignedTo?: string;
      taskType?: string;
      domainId?: string;
      priority?: string;
      status?: string;
    } = {}
  ): Promise<ReviewQueueItem[]> => {
    const params = new URLSearchParams();
    if (filters.assignedTo) params.set("assigned_to", filters.assignedTo);
    if (filters.taskType) params.set("task_type", filters.taskType);
    if (filters.domainId) params.set("domain_id", filters.domainId);
    if (filters.priority) params.set("priority", filters.priority);
    if (filters.status) params.set("status", filters.status);
    const query = params.toString();
    const body = await apiClient.get<{ tasks: unknown[] }>(
      `/api/governance/review/tasks${query ? `?${query}` : ""}`
    );
    return asArray(body.tasks).map(reviewTask);
  },
  reviewTaskDetail: async (
    taskId?: string
  ): Promise<{
    id: string;
    title: string;
    comments: string[];
    evidenceId: string;
    proposedChange: string;
    updatedAt: string | null;
    bundle: ReviewBundle | null;
  }> => {
    if (!taskId)
      return {
        id: "",
        title: "",
        comments: [],
        evidenceId: "",
        proposedChange: "",
        updatedAt: null,
        bundle: null,
      };
    const body = await apiClient.get<{ task: unknown; bundle?: ReviewBundle }>(
      `/api/governance/review/tasks/${encodeURIComponent(taskId)}`
    );
    const task = reviewTask(body.task);
    return {
      id: task.id,
      title: task.title,
      comments: task.comments,
      evidenceId: task.evidenceId,
      proposedChange: task.proposedChange,
      updatedAt: task.updatedAt ?? null,
      bundle: body.bundle ?? null,
    };
  },
  raiseReviewTask: async (payload: {
    target_type: string;
    target_id: string;
    reason: string;
    task_type?: string;
    recommendation?: string;
    domain_id?: string;
  }): Promise<{ id: string; status: string }> => {
    const body = await apiClient.post<{ task: AnyRecord }>("/api/governance/review/tasks", payload);
    return { id: str(rec(body.task).id), status: str(rec(body.task).status, "open") };
  },
  updateReviewTask: async (
    taskId: string,
    payload: { status?: string; assigned_to?: string; resolution?: string; comment?: string }
  ): Promise<{ status: string }> => {
    const body = await apiClient.patch<{ task: AnyRecord }>(
      `/api/governance/review/tasks/${encodeURIComponent(taskId)}`,
      payload
    );
    return { status: str(rec(body.task).status, "updated") };
  },
  reviewBundle: async (taskId: string): Promise<ReviewBundle> =>
    (
      await apiClient.get<{ bundle: ReviewBundle }>(
        `/api/governance/review/tasks/${encodeURIComponent(taskId)}/bundle`
      )
    ).bundle,
  decideReviewBundle: async (
    taskId: string,
    payload: {
      decision: "approved" | "rejected";
      resolution: string;
      entity_resolution_targets?: Record<string, string>;
    }
  ): Promise<{ status: string }> => {
    const body = await apiClient.post<{ task: AnyRecord }>(
      `/api/governance/review/tasks/${encodeURIComponent(taskId)}/decision`,
      payload
    );
    return { status: str(rec(body.task).status, "updated") };
  },
  applyReviewAction: async (
    taskId: string,
    payload: {
      action: SemanticReviewAction;
      note: string;
      expected_updated_at?: string | null;
      survivor_id?: string;
      source_idea_block_id?: string;
      target_idea_block_id?: string;
    }
  ): Promise<{ status: string; action: SemanticReviewAction; relationId: string | null }> => {
    const body = await apiClient.post<{
      task: AnyRecord;
      action: SemanticReviewAction;
      relation_id?: string | null;
    }>(`/api/governance/review/tasks/${encodeURIComponent(taskId)}/action`, payload);
    return {
      status: str(rec(body.task).status, "updated"),
      action: body.action,
      relationId: body.relation_id ?? null,
    };
  },
  lintFindings: async (): Promise<
    Array<{
      id: string;
      rule: string;
      severity: string;
      summary: string;
      canCreateReviewTask: boolean;
    }>
  > => {
    const body = await apiClient.get<{ findings: unknown[] }>("/api/governance/health/lint-report");
    return asArray<AnyRecord>(body.findings).map((finding, index) => ({
      id: str(finding.target_id, `finding-${index}`),
      rule: str(finding.rule_id, "lint"),
      severity: str(finding.severity, "review"),
      summary: str(finding.message, "Review required"),
      canCreateReviewTask: true,
    }));
  },
  createLintReviewTasks: async (): Promise<{ created: number }> => ({
    created: asArray(
      (
        await apiClient.get<{ review_tasks_created: unknown[] }>(
          "/api/governance/health/lint-report?create_tasks=true"
        )
      ).review_tasks_created
    ).length,
  }),
  detectContradictions: (): Promise<{ review_tasks: Array<Record<string, unknown>> }> =>
    apiClient.post("/api/governance/contradictions/detect", {}),
  detectBlindSpots: (): Promise<{ review_tasks: Array<Record<string, unknown>> }> =>
    apiClient.post("/api/governance/blind-spots/detect", {}),
};
