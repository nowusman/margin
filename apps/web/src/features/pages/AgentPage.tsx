import React, { useState } from "react";
import { EmptyState, ErrorState, LoadingState } from "../../components/layout/States";
import { MarkdownContent } from "../../components/rich/MarkdownContent";
import { agentClient } from "../../lib/apiAgent";
import type { AgentRunDetail, AgentRunSummary } from "../../lib/apiTypes";
import { useCommand, useLoad } from "../../lib/hooks";
import { AppRoute } from "./types";

export type AgentPageProps = {
  route: AppRoute;
  params: Record<string, string>;
  onNavigate: (_route: AppRoute, _params?: Record<string, string>) => void;
};

export function AgentPage({ route, params, onNavigate }: AgentPageProps): React.JSX.Element {
  const runId = params.runId ?? "";
  const [question, setQuestion] = useState("");
  const command = useCommand();

  const runs = useLoad("agent-runs", agentClient.runs, { enabled: route === "/agent" });
  const playbooks = useLoad("agent-playbooks", agentClient.playbooks, {
    enabled: route === "/agent",
  });
  const detail = useLoad(
    runId ? `agent-run-${runId}` : "agent-run-none",
    () => agentClient.run(runId),
    { enabled: (route === "/agent" || route === "/agent/:runId") && Boolean(runId) }
  );

  const refresh = (): void => {
    runs.refresh();
    detail.refresh();
  };

  return (
    <section className="page-grid">
      <article className="card">
        <p className="eyebrow">The agent</p>
        <h2>Ask it to go and look</h2>
        <p className="subtle">
          The agent reads the corpus and proposes. It cannot decide: every change it suggests waits
          for you, and what it reads is bounded by the same access rules you have.
        </p>
        <div className="stacked-form">
          <label className="field">
            <span>Your question</span>
            <textarea
              rows={3}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="Which records describe how a payment is settled?"
            />
          </label>
          <div className="actions-row">
            <button
              type="button"
              disabled={!question.trim()}
              onClick={() =>
                command.runCommand("Start the agent", () =>
                  agentClient.createRun({ question: question.trim() }).then((run) => {
                    onNavigate("/agent/:runId", { runId: run.id });
                    runs.refresh();
                  })
                )
              }
            >
              Start a run
            </button>
          </div>
        </div>
        {command.message ? <p className="subtle">{command.message}</p> : null}
      </article>

      <article className="card">
        <h3>Your runs</h3>
        {runs.loading ? <LoadingState label="Loading your runs..." /> : null}
        <ErrorState error={runs.error} />
        <ul className="stack-list" data-testid="agent-runs">
          {(runs.data ?? []).length === 0 ? (
            <li className="subtle">You have not started a run yet.</li>
          ) : null}
          {(runs.data ?? []).map((run: AgentRunSummary) => (
            <li key={run.id} data-testid="agent-run">
              <strong>{run.question}</strong>
              <span className="subtle">
                {run.state} · {run.iterationsUsed}/{run.iterationBudget} iterations ·{" "}
                {run.tokensUsed} tokens
              </span>
              <button type="button" onClick={() => onNavigate("/agent/:runId", { runId: run.id })}>
                Open
              </button>
            </li>
          ))}
        </ul>
      </article>

      {runId ? (
        <article className="card" data-testid="agent-run-detail">
          <h3>The run</h3>
          {detail.loading ? <LoadingState label="Loading the run..." /> : null}
          <ErrorState error={detail.error} />
          {detail.data ? <RunDetail detail={detail.data} onChange={refresh} /> : null}
        </article>
      ) : null}

      <article className="card">
        <h3>Playbooks</h3>
        <p className="subtle">
          Authored guidance that says how a domain is read. A playbook is never evidence.
        </p>
        {playbooks.loading ? <LoadingState label="Loading playbooks..." /> : null}
        <ul className="stack-list" data-testid="agent-playbooks">
          {(playbooks.data ?? []).length === 0 ? (
            <li className="subtle">No playbook governs the domains you can read.</li>
          ) : null}
          {(playbooks.data ?? []).map((playbook) => (
            <li key={`${playbook.domainId}-${playbook.name}`} data-testid="agent-playbook">
              <strong>{playbook.name}</strong>
              <span className="subtle">
                version {playbook.version} · {playbook.domainId || "every domain"}
              </span>
              <span className="subtle">{playbook.description}</span>
            </li>
          ))}
        </ul>
      </article>
    </section>
  );
}

function RunDetail({
  detail,
  onChange,
}: {
  detail: AgentRunDetail;
  onChange: () => void;
}): React.JSX.Element {
  const command = useCommand();
  const [answer, setAnswer] = useState<Record<string, string>>({});

  return (
    <>
      <p className="subtle">
        {detail.state} · {detail.iterationsUsed}/{detail.iterationBudget} iterations ·{" "}
        {detail.tokensUsed} of {detail.tokenBudget} tokens
      </p>
      {detail.answer ? <MarkdownContent content={detail.answer} /> : null}
      {detail.error ? <p className="state-error">{detail.error}</p> : null}
      <div className="actions-row">
        <button
          type="button"
          onClick={() =>
            command.runCommand("Cancel the run", () =>
              agentClient.cancelRun(detail.id).then(() => onChange())
            )
          }
        >
          Cancel the run
        </button>
      </div>

      {detail.questions.filter((question) => question.state === "pending").length > 0 ? (
        <div className="callout" data-testid="agent-questions">
          <h4>It needs your answer</h4>
          {detail.questions
            .filter((question) => question.state === "pending")
            .map((question) => (
              <div key={question.id} className="stacked-form">
                <p>
                  <strong>{question.title}</strong>
                </p>
                <p className="subtle">{question.description}</p>
                {question.fields.map((field) => (
                  <label className="field" key={field.key}>
                    <span>{field.label}</span>
                    {field.options.length > 0 ? (
                      <select
                        value={answer[field.key] ?? field.default}
                        onChange={(event) =>
                          setAnswer((current) => ({ ...current, [field.key]: event.target.value }))
                        }
                      >
                        {field.options.map((option) => (
                          <option key={option} value={option}>
                            {option}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        value={answer[field.key] ?? field.default}
                        onChange={(event) =>
                          setAnswer((current) => ({ ...current, [field.key]: event.target.value }))
                        }
                      />
                    )}
                  </label>
                ))}
                <button
                  type="button"
                  onClick={() =>
                    command.runCommand("Answer the agent", () =>
                      agentClient.answerQuestion(question.id, answer).then(() => onChange())
                    )
                  }
                >
                  Answer
                </button>
              </div>
            ))}
        </div>
      ) : null}

      {detail.proposals.length > 0 ? (
        <div data-testid="agent-proposals">
          <h4>Proposed changes</h4>
          <ul className="stack-list">
            {detail.proposals.map((proposal) => (
              <li key={proposal.id} data-testid="agent-proposal">
                <strong>{proposal.summary || proposal.path || proposal.targetId}</strong>
                <span className="subtle">{proposal.state}</span>
                {proposal.diff ? (
                  <pre className="technical-details" data-testid="agent-proposal-diff">
                    {proposal.diff}
                  </pre>
                ) : null}
                {proposal.state === "proposed" ? (
                  <div className="actions-row">
                    <button
                      type="button"
                      onClick={() =>
                        command.runCommand("Approve the change", () =>
                          agentClient.approveProposal(proposal.id).then(() => onChange())
                        )
                      }
                    >
                      Approve
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        command.runCommand("Refuse the change", () =>
                          agentClient.rejectProposal(proposal.id).then(() => onChange())
                        )
                      }
                    >
                      Refuse
                    </button>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div data-testid="agent-events">
        <h4>What it did</h4>
        {detail.events.length === 0 ? (
          <EmptyState label="Nothing has happened yet." />
        ) : (
          <ul className="stack-list">
            {detail.events.map((event) => (
              <li key={event.id} data-testid="agent-event">
                <strong>{event.kind}</strong>
                <span className="subtle">{event.createdAt}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
