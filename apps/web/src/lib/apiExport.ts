import { apiClient } from "./api-client";
import { asArray, rec, str } from "./apiMappers";
import type { DomainExport } from "./apiTypes";

const exportBundle = (value: unknown): DomainExport => {
  const item = rec(value);
  const manifest = rec(item.manifest);
  return {
    exportId: str(item.export_id),
    domainId: str(item.domain_id),
    state: str(item.state, "pending"),
    contentHash: str(item.content_hash),
    manifest: {
      format_version: str(manifest.format_version),
      domain_id: str(manifest.domain_id),
      generated_at: str(manifest.generated_at),
      content_hash: str(manifest.content_hash),
      record_kinds: rec(manifest.record_kinds) as Record<string, number>,
      included_record_kinds: asArray<string>(manifest.included_record_kinds).filter(Boolean),
      excluded_by_construction: asArray<string>(manifest.excluded_by_construction).filter(Boolean),
    },
    error: str(item.error),
    createdAt: str(item.created_at),
    completedAt: str(item.completed_at),
  };
};

export const exportClient = {
  request: (domainId: string): Promise<DomainExport> =>
    apiClient
      .post<unknown>("/api/exports", { domain_id: domainId })
      .then((payload) => exportBundle(rec(payload).export ?? payload)),

  read: (exportId: string): Promise<DomainExport> =>
    apiClient
      .get<unknown>(`/api/exports/${encodeURIComponent(exportId)}`)
      .then((payload) => exportBundle(payload)),

  download: (exportId: string): Promise<Blob> =>
    apiClient.blob(`/api/exports/${encodeURIComponent(exportId)}/download`),
};
