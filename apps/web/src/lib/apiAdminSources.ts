import { fallbackName, shortId } from "./display";
import { apiClient } from "./api-client";
import type {
  AuditEvent,
  ExtractionReport,
  IngestionReceipt,
  ModelRegistryItem,
  ReprocessingDiff,
  SourceElement,
  SourceHistory,
  SourcePage,
  SourceSummary,
  SourceVersion,
} from "./apiTypes";

type AnyRecord = Record<string, unknown>;

const asArray = <T>(value: unknown): T[] => (Array.isArray(value) ? (value as T[]) : []);
const rec = (value: unknown): AnyRecord =>
  value && typeof value === "object" ? (value as AnyRecord) : {};
const str = (value: unknown, fallback = ""): string =>
  typeof value === "string" ? value : fallback;
const num = (value: unknown, fallback = 0): number =>
  typeof value === "number" ? value : fallback;

const fileNameFromPath = (value: string): string => {
  const normalized = value.split("?")[0].split("#")[0].replace(/\\/g, "/").replace(/\/+$/, "");
  return normalized.split("/").pop() ?? "";
};

const normalizeDomainSelection = (domainId: string | undefined): string | undefined => {
  if (!domainId) return undefined;
  const normalized = domainId.trim();
  return normalized && normalized.toLowerCase() !== "all domains" ? normalized : undefined;
};

const modelRuntime = (value: unknown): ModelRegistryItem["runtime"] => {
  return str(value, "openai-compatible");
};

const modelRegistryItem = (value: unknown): ModelRegistryItem => {
  const item = rec(value);
  return {
    id: str(item.id),
    displayName: str(item.displayName),
    purpose: str(item.purpose, "extraction") as ModelRegistryItem["purpose"],
    runtime: modelRuntime(item.runtime),
    modelName: str(item.modelName),
    endpointUrl: str(item.endpointUrl),
    secretConfigured: Boolean(item.secretConfigured),
    capabilities: rec(item.capabilities),
    contextLength: num(item.contextLength),
    maxTokens: num(item.maxTokens),
    temperatureDefault: num(item.temperatureDefault),
    license: str(item.license),
    hardwareProfile: str(item.hardwareProfile),
    status: str(item.status, "disabled") as ModelRegistryItem["status"],
    defaultScopes: asArray<string>(item.defaultScopes),
    systemPrompt: str(item.systemPrompt),
    lastTestStatus: typeof item.lastTestStatus === "string" ? item.lastTestStatus : null,
    lastTestLatencyMs: typeof item.lastTestLatencyMs === "number" ? item.lastTestLatencyMs : null,
    lastTestDetail: typeof item.lastTestDetail === "string" ? item.lastTestDetail : null,
    lastTestedAt: typeof item.lastTestedAt === "string" ? item.lastTestedAt : null,
  };
};

const sourceSummary = (value: unknown): SourceSummary => {
  const item = rec(value);
  const id = str(item.id);
  const originalFilename = str(
    rec(item.metadata).upload_filename,
    fileNameFromPath(str(item.source_uri))
  );
  return {
    id,
    title: fallbackName(originalFilename, fallbackName(str(item.title), `Source ${shortId(id)}`)),
    originalFilename,
    sourceUri: str(item.source_uri),
    mediaType: str(item.media_type),
    domainId: str(item.domain_id),
    category: str(item.category, "Uncategorized"),
    sensitivity: str(item.sensitivity),
    status: str(item.status, "indexed"),
    owner: str(item.owner, str(rec(item.metadata).owner)),
    steward: str(item.steward),
    createdAt: str(item.created_at),
    pages: asArray<AnyRecord>(item.pages).map(sourcePage),
    elements: asArray<AnyRecord>(item.elements).map(sourceElement),
  };
};

const sourcePage = (value: unknown): SourcePage => {
  const item = rec(value);
  return {
    id: str(item.id),
    sourceVersionId: str(item.source_version_id),
    pageNumber: num(item.page_number),
    width: typeof item.width === "number" ? item.width : null,
    height: typeof item.height === "number" ? item.height : null,
    dpi: typeof item.dpi === "number" ? item.dpi : null,
    assetKey: typeof item.asset_key === "string" ? item.asset_key : null,
    checksum:
      typeof item.content_checksum === "string"
        ? item.content_checksum
        : typeof item.checksum === "string"
          ? item.checksum
          : null,
  };
};

const sourceElement = (value: unknown): SourceElement => {
  const item = rec(value);
  return {
    id: str(item.id),
    sourceVersionId: str(item.source_version_id),
    sourcePageId: typeof item.source_page_id === "string" ? item.source_page_id : null,
    parentElementId: typeof item.parent_element_id === "string" ? item.parent_element_id : null,
    nativeElementId: typeof item.native_element_id === "string" ? item.native_element_id : null,
    kind: str(item.kind, "text"),
    elementType: str(item.element_type, str(item.kind, "text")),
    ordinal: num(item.ordinal),
    text: str(item.text),
    checksum: str(item.checksum),
    pageNumber: typeof item.page_number === "number" ? item.page_number : null,
    charStart: typeof item.char_start === "number" ? item.char_start : null,
    charEnd: typeof item.char_end === "number" ? item.char_end : null,
    boundingBox: item.bounding_box,
    assetKey: typeof item.asset_key === "string" ? item.asset_key : null,
    converterMethod: typeof item.converter_method === "string" ? item.converter_method : null,
    converterVersion: typeof item.converter_version === "string" ? item.converter_version : null,
    confidence: typeof item.confidence === "number" ? item.confidence : null,
  };
};

const sourceVersion = (value: unknown): SourceVersion => {
  const item = rec(value);
  return {
    id: str(item.id),
    versionHash: str(item.version_sha256),
    createdAt: str(item.created_at),
    converterName: str(item.converter_name),
    converterVersion: str(item.converter_version),
    status: str(item.status, "unknown"),
    supersededByVersionId:
      typeof item.superseded_by_version_id === "string" ? item.superseded_by_version_id : null,
  };
};

const reprocessingDiff = (value: unknown): ReprocessingDiff => {
  const item = rec(value);
  return {
    id: str(item.id),
    previousVersionId:
      typeof item.previous_version_id === "string" ? item.previous_version_id : null,
    currentVersionId: str(item.current_version_id),
    extractionRunId: typeof item.extraction_run_id === "string" ? item.extraction_run_id : null,
    addedClaimIds: asArray(item.added_claim_ids),
    removedClaimIds: asArray(item.removed_claim_ids),
    modifiedClaims: asArray(item.modified_claims),
    changedEntityResolutions: asArray(item.changed_entity_resolutions),
    addedRelationshipIds: asArray(item.added_relationship_ids),
    removedRelationshipIds: asArray(item.removed_relationship_ids),
    newlyUnresolved: asArray(item.newly_unresolved),
    createdAt: str(item.created_at),
  };
};

export const adminClient = {
  models: async (): Promise<ModelRegistryItem[]> =>
    asArray(await apiClient.get<unknown[]>("/api/admin/models")).map(modelRegistryItem),
  modelHealth: async () =>
    asArray<AnyRecord>(await apiClient.get<unknown[]>("/api/admin/models/health")).map((item) => ({
      provider: str(item.provider),
      status: str(item.status),
      detail: str(item.detail),
    })),
  modelConfigurationChecks: () =>
    apiClient.get<Array<{ id: string; configured: boolean }>>(
      "/api/admin/models/configuration-checks"
    ),
  testModel: (modelId: string) =>
    apiClient.post<{ modelId: string; status: string; latencyMs: number; detail: string }>(
      `/api/admin/models/${encodeURIComponent(modelId)}/test`
    ),
  access: () =>
    apiClient.get<
      Array<{
        user: string;
        groups: string[];
        domains: string[];
        categories: string[];
        sensitivities: string[];
      }>
    >("/api/admin/access"),
  accessCheck: (payload: Record<string, unknown> = {}) =>
    apiClient.post<{ user: string; canSee: string[]; denied: string[]; decisionTrace: string }>(
      "/api/admin/access/check",
      payload
    ),
  systemHealth: () =>
    apiClient.get<{
      status: string;
      database: string;
      converterDependencies: string;
      objectStorage: string;
      redis: string;
    }>("/api/admin/system-health"),
  auditEvents: async (): Promise<AuditEvent[]> => {
    const body = await apiClient.get<{ audit_events: unknown[] }>("/api/audit-events?limit=20");
    return asArray<AnyRecord>(body.audit_events).map((event) => ({
      id: str(event.id),
      actor: str(event.actor),
      action: str(event.action),
      target: str(event.target_id, str(event.target_type)),
      at: str(event.created_at),
    }));
  },
  addModel: async (payload: Partial<ModelRegistryItem>): Promise<ModelRegistryItem | null> => {
    const body = await apiClient.post<AnyRecord>("/api/admin/models", payload);
    const model = rec(body.model);
    return Object.keys(model).length > 0 ? modelRegistryItem(model) : null;
  },
  updateModel: (modelId: string, payload: Partial<ModelRegistryItem>) =>
    apiClient.patch(`/api/admin/models/${encodeURIComponent(modelId)}`, payload),
  deleteModel: (modelId: string) =>
    apiClient.delete(`/api/admin/models/${encodeURIComponent(modelId)}`),
  saveAccessProfile: (
    user: string,
    payload: { groups: string[]; domains: string[]; categories: string[]; sensitivities: string[] },
    existing: boolean
  ) =>
    existing
      ? apiClient.patch(`/api/admin/access/${encodeURIComponent(user)}`, payload)
      : apiClient.post(`/api/admin/access?user=${encodeURIComponent(user)}`, payload),
  deleteAccessProfile: (user: string) =>
    apiClient.delete(`/api/admin/access/${encodeURIComponent(user)}`),
};

export const sourcesClient = {
  upload: (
    file: Blob,
    filename: string,
    metadata: {
      domain_id?: string;
      category?: string;
      sensitivity?: string;
      source_uri?: string;
      source_version?: string;
      source_owner?: string;
      source_language?: string;
      extraction_mode?: string;
      extraction_priority?: string;
      review_owner?: string;
      source_type?: string;
      source_date?: string;
    } = {}
  ) => {
    const formData = new globalThis.FormData();
    formData.append("file", file, filename);
    Object.entries(metadata).forEach(([key, value]) => {
      const nextValue = key === "domain_id" ? normalizeDomainSelection(value) : value;
      if (nextValue) formData.append(key, nextValue);
    });
    return apiClient.post<AnyRecord>("/api/sources", formData);
  },
  list: async (): Promise<SourceSummary[]> => {
    const body = await apiClient.get<{ sources: unknown[] }>("/api/sources");
    return asArray<AnyRecord>(body.sources).map(sourceSummary);
  },
  get: async (sourceId: string): Promise<SourceSummary | null> => {
    const body = await apiClient.get<AnyRecord>(`/api/sources/${encodeURIComponent(sourceId)}`);
    const payload = rec(body.source_document);
    return Object.keys(payload).length > 0 ? sourceSummary(payload) : null;
  },
  file: (sourceId: string, signal?: AbortSignal): Promise<Blob> =>
    apiClient.blob(`/api/sources/${encodeURIComponent(sourceId)}/file`, { signal }),
  previewFile: (sourceId: string, signal?: AbortSignal): Promise<Blob> =>
    apiClient.blob(`/api/sources/${encodeURIComponent(sourceId)}/preview-file`, { signal }),
  pages: async (sourceId: string): Promise<SourcePage[]> => {
    const body = await apiClient.get<{ pages: unknown[] }>(
      `/api/sources/${encodeURIComponent(sourceId)}/pages`
    );
    return asArray<AnyRecord>(body.pages).map(sourcePage);
  },
  elements: async (sourceId: string): Promise<SourceElement[]> => {
    const body = await apiClient.get<{ elements: unknown[] }>(
      `/api/sources/${encodeURIComponent(sourceId)}/elements`
    );
    return asArray<AnyRecord>(body.elements).map(sourceElement);
  },
  versions: async (sourceId: string): Promise<SourceVersion[]> => {
    const body = await apiClient.get<{ versions: unknown[] }>(
      `/api/sources/${encodeURIComponent(sourceId)}/versions`
    );
    return asArray<AnyRecord>(body.versions).map(sourceVersion);
  },
  history: async (sourceId: string): Promise<SourceHistory> => {
    const [versions, pages, reprocessingDiffs] = await Promise.all([
      sourcesClient.versions(sourceId),
      sourcesClient.pages(sourceId),
      sourcesClient.reprocessingDiffs(sourceId),
    ]);
    return { versions, pages, reprocessingDiffs };
  },
  extractionReport: async (sourceId: string): Promise<ExtractionReport> => {
    const body = await apiClient.get<AnyRecord>(
      `/api/sources/${encodeURIComponent(sourceId)}/extraction-report`
    );
    return {
      sourceDocumentId: sourceId,
      report: rec(body.report),
    };
  },
  ingestionReceipt: (sourceId: string): Promise<IngestionReceipt> =>
    apiClient.get(`/api/sources/${encodeURIComponent(sourceId)}/ingestion-receipt`),
  delete: (sourceId: string) => apiClient.delete(`/api/sources/${encodeURIComponent(sourceId)}`),
  reprocess: (sourceId: string) =>
    apiClient.post(`/api/sources/${encodeURIComponent(sourceId)}/reprocess`),
  reprocessingDiffs: async (sourceId: string): Promise<ReprocessingDiff[]> => {
    const body = await apiClient.get<{ diffs: unknown[] }>(
      `/api/sources/${encodeURIComponent(sourceId)}/reprocessing-diffs`
    );
    return asArray<AnyRecord>(body.diffs).map(reprocessingDiff);
  },
};
