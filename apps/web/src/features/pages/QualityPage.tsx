import React from "react";
import { EmptyState, ErrorState, LoadingState } from "../../components/layout/States";
import { DataTable } from "../../components/tables";
import { governanceClient } from "../../lib/apiGovernance";
import { qualityClient } from "../../lib/apiQuality";
import type { EvaluationRun } from "../../lib/apiTypes";
import { shortId, technicalIdLabel } from "../../lib/display";
import { useCommand, useLoad } from "../../lib/hooks";
import { AppRoute } from "./types";

export type QualityPageProps = {
  route: AppRoute;
  params: Record<string, string>;
};

export function QualityPage({ route, params }: QualityPageProps): React.JSX.Element {
  const command = useCommand();
  const lintFindings = useLoad("quality-lint", governanceClient.lintFindings, {
    enabled: route === "/quality/lint",
  });
  const evaluationRuns = useLoad("quality-evaluation-runs", qualityClient.evaluationRuns, {
    enabled: route === "/quality/evaluations" || route === "/quality/evaluations/:runId",
  });
  const selectedRunId = params.runId || "";
  const evaluationRun = useLoad(
    `quality-evaluation-run-${selectedRunId || "none"}`,
    () => qualityClient.evaluationRun(selectedRunId),
    {
      enabled:
        (route === "/quality/evaluations" || route === "/quality/evaluations/:runId") &&
        Boolean(selectedRunId),
    }
  );

  if (route === "/quality/lint") {
    return (
      <section className="page-grid">
        <article className="card">
          <h2>Lint findings</h2>
          {lintFindings.loading ? <LoadingState label="Loading lint findings..." /> : null}
          <ErrorState error={lintFindings.error} />
          {lintFindings.data && lintFindings.data.length > 0 ? (
            <DataTable
              rows={lintFindings.data}
              emptyLabel="No lint findings."
              getRowKey={(row) => row.id}
              columns={[
                { header: "Rule", render: (row) => row.rule, sortValue: (row) => row.rule },
                {
                  header: "Severity",
                  render: (row) => row.severity,
                  sortValue: (row) => row.severity,
                },
                {
                  header: "Summary",
                  render: (row) => row.summary,
                  sortValue: (row) => row.summary,
                },
                {
                  header: "Action",
                  render: (row) => (row.canCreateReviewTask ? "Create review task" : "n/a"),
                },
              ]}
            />
          ) : (
            !lintFindings.loading && <EmptyState label="No lint findings available." />
          )}
          <div className="actions-row">
            <button
              type="button"
              disabled={command.busy}
              onClick={() =>
                command.runCommand(
                  "Create lint review tasks",
                  async () => {
                    const result = await governanceClient.createLintReviewTasks();
                    lintFindings.refresh();
                    return result;
                  },
                  (result) => {
                    const created = (result as { created?: number }).created ?? 0;
                    return `Created ${created} lint review task${created === 1 ? "" : "s"}.`;
                  }
                )
              }
            >
              Create lint review tasks
            </button>
            <button
              type="button"
              disabled={command.busy}
              onClick={() =>
                command.runCommand("Detect contradictions", governanceClient.detectContradictions)
              }
            >
              Detect contradictions
            </button>
            <button
              type="button"
              disabled={command.busy}
              onClick={() =>
                command.runCommand("Detect blind spots", governanceClient.detectBlindSpots)
              }
            >
              Detect blind spots
            </button>
          </div>
          {command.message ? <p role="status">{command.message}</p> : null}
        </article>
      </section>
    );
  }

  if (route === "/quality/evaluations" || route === "/quality/evaluations/:runId") {
    return (
      <section className="page-grid">
        <article className="card">
          <h2>Stored runs</h2>
          {evaluationRuns.loading || evaluationRun.loading ? (
            <LoadingState label="Loading evaluation runs..." />
          ) : null}
          <ErrorState error={evaluationRuns.error || evaluationRun.error} />
          {evaluationRuns.data && evaluationRuns.data.length > 0 ? (
            <DataTable
              rows={evaluationRuns.data}
              emptyLabel="No evaluation runs."
              getRowKey={(row) => row.id}
              columns={[
                {
                  header: "Run",
                  render: (row) => `${row.corpusVersion} (${row.evaluationMode})`,
                  sortValue: (row) => row.corpusVersion,
                },
                { header: "Status", render: (row) => row.status, sortValue: (row) => row.status },
                {
                  header: "Gate failures",
                  render: (row) => row.gateFailures.length,
                  sortValue: (row) => row.gateFailures.length,
                },
              ]}
            />
          ) : (
            !evaluationRuns.loading && <EmptyState label="No stored evaluation runs available." />
          )}
          {evaluationRun.data ? (
            <>
              <p>
                Selected run: {evaluationRun.data.corpusVersion} (
                {evaluationRun.data.evaluationMode})
              </p>
              <p>
                Fixtures: {evaluationRun.data.fixtureCount} - status: {evaluationRun.data.status}
              </p>
              {evaluationRun.data.metricWarning ? <p>{evaluationRun.data.metricWarning}</p> : null}
              <h3>Gate failures</h3>
              {evaluationRun.data.gateFailures.length > 0 ? (
                <ul>
                  {evaluationRun.data.gateFailures.map((failure) => (
                    <li key={failure}>{failure}</li>
                  ))}
                </ul>
              ) : (
                <EmptyState label="No evaluation gate failures." />
              )}
              <h3>Metrics</h3>
              {Object.entries(evaluationRun.data.metrics).length > 0 ? (
                Object.entries(evaluationRun.data.metrics).map(([key, value]) => (
                  <p key={key}>
                    {key}: {String(value)}
                  </p>
                ))
              ) : (
                <EmptyState label="No metrics recorded." />
              )}
              <details className="technical-details">
                <summary>Technical details</summary>
                <p className="technical-id">{technicalIdLabel(evaluationRun.data.id)}</p>
              </details>
              <EvaluationIssueList
                label="False positives"
                issues={evaluationRun.data.falsePositives}
              />
              <EvaluationIssueList
                label="False negatives"
                issues={evaluationRun.data.falseNegatives}
              />
            </>
          ) : (
            !evaluationRun.loading && (
              <EmptyState
                label={
                  params.runId
                    ? `Evaluation run ${shortId(params.runId)} was not found.`
                    : "Select an evaluation run for details."
                }
              />
            )
          )}
        </article>
      </section>
    );
  }

  return <EmptyState label="Choose a supported quality view." />;
}

function EvaluationIssueList({
  label,
  issues,
}: {
  label: string;
  issues: EvaluationRun["falsePositives"];
}): React.JSX.Element {
  return (
    <section>
      <h3>{label}</h3>
      {issues.length > 0 ? (
        <ul>
          {issues.map((issue) => (
            <li key={`${issue.fixtureId}-${issue.expectedId}`}>
              <strong>{issue.fixtureId}</strong>: {issue.expectedId}
              {issue.sourceLinks.length > 0 ? (
                <span>
                  {" "}
                  {issue.sourceLinks.map((link, index) => (
                    <span key={`${link.url}-${index}`}>
                      <a href={link.url}>Source evidence{link.page ? ` p.${link.page}` : ""}</a>
                      {link.quote ? `: ${link.quote}` : ""}
                      {index < issue.sourceLinks.length - 1 ? "; " : ""}
                    </span>
                  ))}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState label={`No ${label.toLowerCase()}.`} />
      )}
    </section>
  );
}
