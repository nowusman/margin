import { apiClient } from "./api-client";
import { asArray, bool, num, rec, str } from "./apiMappers";
import type {
  AgentEvent,
  AgentPlaybook,
  AgentProposal,
  AgentQuestion,
  AgentRunDetail,
  AgentRunSummary,
} from "./apiTypes";

export type AgentRunInput = {
  question: string;
  domainId?: string;
  iterationBudget?: number;
  tokenBudget?: number;
  writeGranted?: boolean;
};

const runSummary = (value: unknown): AgentRunSummary => {
  const item = rec(value);
  return {
    id: str(item.id),
    principalId: str(item.principal_id),
    domainId: str(item.domain_id),
    question: str(item.question),
    state: str(item.state, "pending"),
    iterationBudget: num(item.iteration_budget),
    tokenBudget: num(item.token_budget),
    iterationsUsed: num(item.iterations_used),
    tokensUsed: num(item.tokens_used),
    writeGranted: bool(item.write_granted),
    answer: str(item.answer),
    error: str(item.error),
    createdAt: str(item.created_at),
    completedAt: str(item.completed_at),
  };
};

const event = (value: unknown): AgentEvent => {
  const item = rec(value);
  return {
    id: str(item.id),
    sequence: num(item.sequence),
    kind: str(item.kind),
    payload: rec(item.payload),
    createdAt: str(item.created_at),
  };
};

const proposal = (value: unknown): AgentProposal => {
  const item = rec(value);
  return {
    id: str(item.id),
    runId: str(item.run_id),
    targetType: str(item.target_type),
    targetId: str(item.target_id),
    path: str(item.path),
    summary: str(item.summary),
    diff: str(item.diff),
    state: str(item.state, "proposed"),
    reviewedBy: str(item.reviewed_by),
    reviewedAt: str(item.reviewed_at),
    createdAt: str(item.created_at),
  };
};

const question = (value: unknown): AgentQuestion => {
  const item = rec(value);
  return {
    id: str(item.id),
    requestKey: str(item.request_key),
    title: str(item.title),
    description: str(item.description),
    fields: asArray<unknown>(item.fields).map((entry) => {
      const field = rec(entry);
      return {
        key: str(field.key),
        label: str(field.label),
        type: str(field.type, "text"),
        options: asArray<string>(field.options).filter(Boolean),
        default: str(field.default),
      };
    }),
    state: str(item.state, "pending"),
    response: rec(item.response),
  };
};

const playbook = (value: unknown): AgentPlaybook => {
  const item = rec(value);
  return {
    name: str(item.name),
    description: str(item.description),
    version: str(item.version),
    domainId: str(item.domain_id),
    appliesTo: asArray<string>(item.applies_to).filter(Boolean),
    path: str(item.path),
    supportingFiles: asArray<string>(item.supporting_files).filter(Boolean),
  };
};

export const agentClient = {
  runs: (): Promise<AgentRunSummary[]> =>
    apiClient
      .get<unknown>("/api/agent/runs")
      .then((payload) => asArray<unknown>(rec(payload).runs).map(runSummary)),

  createRun: (input: AgentRunInput): Promise<AgentRunSummary> =>
    apiClient
      .post<unknown>("/api/agent/runs", {
        question: input.question,
        domain_id: input.domainId ?? null,
        iteration_budget: input.iterationBudget ?? 8,
        token_budget: input.tokenBudget ?? 32000,
        write_granted: input.writeGranted ?? false,
      })
      .then((payload) => runSummary(rec(payload).run)),

  run: (runId: string): Promise<AgentRunDetail> =>
    apiClient.get<unknown>(`/api/agent/runs/${encodeURIComponent(runId)}`).then((payload) => {
      const item = rec(payload);
      return {
        ...runSummary(item.run),
        events: asArray<unknown>(item.events).map(event),
        proposals: asArray<unknown>(item.proposals).map(proposal),
        questions: asArray<unknown>(item.questions).map(question),
      };
    }),

  cancelRun: (runId: string): Promise<AgentRunSummary> =>
    apiClient
      .post<unknown>(`/api/agent/runs/${encodeURIComponent(runId)}/cancel`)
      .then((payload) => runSummary(rec(payload).run)),

  approveProposal: (proposalId: string): Promise<AgentProposal> =>
    apiClient
      .post<unknown>(`/api/agent/proposals/${encodeURIComponent(proposalId)}/approve`)
      .then((payload) => proposal(rec(payload).proposal)),

  rejectProposal: (proposalId: string): Promise<AgentProposal> =>
    apiClient
      .post<unknown>(`/api/agent/proposals/${encodeURIComponent(proposalId)}/reject`)
      .then((payload) => proposal(rec(payload).proposal)),

  answerQuestion: (requestId: string, response: Record<string, unknown>): Promise<AgentQuestion> =>
    apiClient
      .post<unknown>(`/api/agent/requests/${encodeURIComponent(requestId)}/answer`, { response })
      .then((payload) => question(rec(payload).request)),

  playbooks: (): Promise<AgentPlaybook[]> =>
    apiClient
      .get<unknown>("/api/agent/playbooks")
      .then((payload) => asArray<unknown>(rec(payload).playbooks).map(playbook)),
};
