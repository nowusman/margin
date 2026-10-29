import { apiClient } from "./api-client";
import { asArray, evaluationRun } from "./apiMappers";
import type { EvaluationRun } from "./apiTypes";

type AnyRecord = Record<string, unknown>;

export const qualityClient = {
  evaluationRuns: async (): Promise<EvaluationRun[]> => {
    const body = await apiClient.get<{ runs: unknown[] }>("/api/evaluations/runs");
    return asArray<AnyRecord>(body.runs).map(evaluationRun);
  },
  evaluationRun: async (runId: string): Promise<EvaluationRun | null> => {
    const body = await apiClient.get<{ run: unknown }>(
      `/api/evaluations/runs/${encodeURIComponent(runId)}`
    );
    if (!body.run) return null;
    return evaluationRun(body.run);
  },
};
