import { apiClient } from "./api-client";
import { AnyRecord, asArray, num, rec, str } from "./apiMappers";
import { fallbackName, shortId } from "./display";
import type { IngestionJob } from "./apiTypes";

const ingestionJob = (value: unknown): IngestionJob => {
  const item = rec(value);
  const sourceDocumentId = str(item.source_document_id);
  const report = rec(item.quality_report);
  return {
    id: str(item.id),
    sourceDocumentId,
    sourceName: fallbackName(
      str(item.source_name, str(item.source_document_title)),
      `Source ${shortId(sourceDocumentId)}`
    ),
    state: str(item.state, "unknown"),
    status: str(item.status, str(item.state, "unknown")),
    stage: str(item.stage, str(item.state, "unknown")),
    attempts: num(item.attempts),
    errorMessage: str(item.error_message),
    createdAt: str(item.created_at),
    updatedAt: str(item.updated_at),
    startedAt: str(item.started_at),
    finishedAt: str(item.finished_at),
    qualityReport: report,
    stageDetails: asArray<AnyRecord>(report.stages).map((stage) => ({
      stage: str(stage.stage, "unknown"),
      worker: str(stage.worker, "unknown"),
      adapter: str(stage.adapter, "unknown"),
      startedAt: str(stage.started_at),
      finishedAt: str(stage.finished_at),
      logs: asArray<string>(stage.logs).map((entry) => str(entry)),
      warnings: asArray<string>(stage.warnings).map((entry) => str(entry)),
      outputCounts:
        stage.output_counts && typeof stage.output_counts === "object"
          ? (stage.output_counts as Record<string, unknown>)
          : {},
    })),
  };
};

export const jobsClient = {
  list: async (): Promise<IngestionJob[]> =>
    asArray<AnyRecord>((await apiClient.get<{ jobs: unknown[] }>("/api/jobs")).jobs).map(
      ingestionJob
    ),
  detail: async (jobId: string): Promise<IngestionJob | null> => {
    const body = await apiClient.get<{ ingestion_job: unknown }>(
      `/api/jobs/${encodeURIComponent(jobId)}`
    );
    return body.ingestion_job ? ingestionJob(body.ingestion_job) : null;
  },
  retry: (jobId: string): Promise<{ status: string }> =>
    apiClient.post(`/api/jobs/${encodeURIComponent(jobId)}/retry`),
  cancel: (jobId: string): Promise<{ status: string }> =>
    apiClient.post(`/api/jobs/${encodeURIComponent(jobId)}/cancel`),
};
