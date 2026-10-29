import { apiClient } from "./api-client";
import { adminClient } from "./apiAdminSources";
import { asArray, num, rec, str } from "./apiMappers";
import type {
  AuditEvent,
  DashboardSummary,
  HeatmapDomain,
  IngestionJob,
  ReviewTask,
} from "./apiTypes";

const domainId = (): string | null => {
  if (typeof globalThis.localStorage === "undefined") return null;
  try {
    const value = globalThis.localStorage.getItem("margin_domain_scope");
    return value && value !== "All domains" ? value : null;
  } catch {
    return null;
  }
};

const scopedPath = (path: string): string => {
  const selected = domainId();
  return selected ? `${path}?domain_id=${encodeURIComponent(selected)}` : path;
};

const priority = (value: unknown): "high" | "medium" | "low" => {
  const normalized = str(value, "medium").toLowerCase();
  return normalized === "high" || normalized === "low" ? normalized : "medium";
};

const job = (value: unknown): IngestionJob => {
  const item = rec(value);
  return {
    id: str(item.id),
    sourceDocumentId: str(item.source_document_id),
    sourceName: str(item.source_name),
    state: str(item.state, str(item.status, "unknown")),
    status: str(item.status, "unknown"),
    stage: str(item.stage),
    attempts: num(item.attempts),
    errorMessage: str(item.error_message),
    createdAt: str(item.created_at),
    updatedAt: str(item.updated_at),
    startedAt: str(item.started_at),
    finishedAt: str(item.finished_at),
    qualityReport: rec(item.quality_report),
    stageDetails: asArray<Record<string, unknown>>(item.stage_details).map((stage) => ({
      stage: str(stage.stage),
      worker: str(stage.worker),
      adapter: str(stage.adapter),
      startedAt: str(stage.started_at),
      finishedAt: str(stage.finished_at),
      logs: asArray<string>(stage.logs),
      warnings: asArray<string>(stage.warnings),
      outputCounts: rec(stage.output_counts),
    })),
  };
};

export const dashboardClient = {
  summary: async (): Promise<DashboardSummary> => {
    const body = await apiClient.get<Record<string, unknown>>(scopedPath("/api/dashboard/summary"));
    return {
      reviewTasksAssigned: num(body.review_tasks_assigned),
      staleClaims: num(body.stale_claims),
      expiredWaivers: num(body.expired_waivers),
      recentAdrs: asArray<Record<string, unknown>>(body.recent_adrs).map((adr) => ({
        id: str(adr.id),
        title: str(adr.title),
        date: str(adr.date),
      })),
      topRisks: asArray<Record<string, unknown>>(body.top_risks).map((risk) => ({
        id: str(risk.id),
        title: str(risk.title),
        severity: priority(risk.severity),
      })),
      topDebt: asArray<Record<string, unknown>>(body.top_debt).map((debt) => ({
        id: str(debt.id),
        title: str(debt.title),
        impact: str(debt.impact),
      })),
    };
  },
  heatmap: async (options: { scoped?: boolean } | AbortSignal = {}): Promise<HeatmapDomain[]> => {
    const scoped = "scoped" in options && options.scoped === true;
    const path = scoped ? scopedPath("/api/dashboard/heatmap") : "/api/dashboard/heatmap";
    const body = await apiClient.get<{ domains: unknown[] }>(path);
    return asArray<Record<string, unknown>>(body.domains).map((domain) => ({
      id: str(domain.domain_id),
      name: str(domain.domain_name, str(domain.domain_id)),
      healthScore: num(domain.health),
      staleClaims: num(domain.stale_claims, num(domain.stale_items)),
      risks: num(domain.risk_volume, num(domain.risks)),
      debt: num(domain.debt),
      atdi: num(domain.atdi),
      highSeverityRisks: num(domain.high_severity_risks, num(domain.risks)),
      expiredWaivers: num(domain.expired_waivers),
      unownedEntities: num(domain.unowned_entities),
      missingAdrCoverage: num(domain.missing_adr_coverage),
      missingDrSecurityEvidence: num(domain.missing_dr_security_evidence),
      trend: asArray<number>(domain.trend).map((point) => num(point)),
    }));
  },
  reviewTasks: async (): Promise<ReviewTask[]> => {
    const selected = domainId();
    const query = new URLSearchParams({ assigned_to: "me" });
    if (selected) query.set("domain_id", selected);
    const body = await apiClient.get<{ tasks: unknown[] }>(
      `/api/governance/review/tasks?${query.toString()}`
    );
    return asArray<Record<string, unknown>>(body.tasks).map((task) => ({
      id: str(task.id),
      title: str(task.title, str(task.reason, "Review task")),
      priority: priority(task.priority),
      dueDate: str(task.due_date),
      status: str(task.status) === "in_review" ? "in_review" : "open",
    }));
  },
  recentJobs: async (): Promise<IngestionJob[]> => {
    const body = await apiClient.get<{ jobs: unknown[] }>("/api/jobs");
    return asArray(body.jobs).slice(0, 10).map(job);
  },
  auditEvents: async (): Promise<AuditEvent[]> => adminClient.auditEvents(),
  retryJob: (jobId: string): Promise<{ status: string }> =>
    apiClient.post(`/api/jobs/${encodeURIComponent(jobId)}/retry`),
  cancelJob: (jobId: string): Promise<{ status: string }> =>
    apiClient.post(`/api/jobs/${encodeURIComponent(jobId)}/cancel`),
};
