import { fallbackName, shortId } from "./display";
import type { DiagramSummary, EvaluationRun } from "./apiTypes";

export type AnyRecord = Record<string, unknown>;

export const asArray = <T>(value: unknown): T[] => (Array.isArray(value) ? (value as T[]) : []);
export const rec = (value: unknown): AnyRecord =>
  value && typeof value === "object" ? (value as AnyRecord) : {};
export const str = (value: unknown, fallback = ""): string =>
  typeof value === "string" ? value : fallback;
export const num = (value: unknown, fallback = 0): number =>
  typeof value === "number" ? value : fallback;
export const bool = (value: unknown, fallback = false): boolean =>
  typeof value === "boolean" ? value : fallback;

const evaluationIssue = (value: unknown): EvaluationRun["falsePositives"][number] => {
  const item = rec(value);
  return {
    fixtureId: str(item.fixture_id),
    expectedId: str(item.expected_id),
    actualIds: asArray<string>(item.actual_ids),
    evidenceIds: asArray<string>(item.evidence_ids),
    sourceLinks: asArray<AnyRecord>(item.source_links).map((link) => ({
      sourceDocumentId: str(link.source_document_id),
      sourceElementId: typeof link.source_element_id === "string" ? link.source_element_id : null,
      quote: typeof link.quote === "string" ? link.quote : null,
      page: typeof link.page === "number" ? link.page : null,
      url: str(link.url),
    })),
  };
};

export const evaluationRun = (value: unknown): EvaluationRun => {
  const item = rec(value);
  return {
    id: str(item.id),
    status: str(item.status, "unknown"),
    corpusVersion: str(item.corpus_version, "unknown"),
    evaluationMode: str(item.evaluation_mode, "unknown"),
    startedAt: str(item.started_at),
    completedAt: str(item.completed_at),
    activeExtractionModelId: str(item.active_extraction_model_id),
    metrics: rec(item.metrics),
    gateFailures: asArray<string>(item.gate_failures),
    fixtureCount: num(item.fixture_count),
    falsePositives: asArray<AnyRecord>(item.false_positives).map(evaluationIssue),
    falseNegatives: asArray<AnyRecord>(item.false_negatives).map(evaluationIssue),
    metricWarning: str(item.metric_warning),
  };
};

export const diagramSummary = (value: unknown): DiagramSummary => {
  const item = rec(value);
  const id = str(item.id);
  return {
    id,
    type: str(item.type, "mermaid") as DiagramSummary["type"],
    title: fallbackName(str(item.title), `Diagram ${shortId(id)}`),
    markup: str(item.markup),
    svg: str(item.svg),
    evidence: asArray<AnyRecord>(item.evidence),
  };
};
