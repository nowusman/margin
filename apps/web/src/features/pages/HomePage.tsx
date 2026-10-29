import React, { Suspense, lazy, useMemo, useState } from "react";
import { EmptyState, ErrorState, LoadingState } from "../../components/layout/States";
import { dashboardClient } from "../../lib/apiDashboard";
import { personalClient } from "../../lib/apiPersonal";
import type { HeatmapDomain } from "../../lib/apiTypes";
import { useCommand, useLoad } from "../../lib/hooks";
import { AppRoute } from "./types";

const HeatmapChart = lazy(async () => {
  const module = await import("../../components/dashboard/HeatmapChart");
  return { default: module.HeatmapChart };
});

const metrics = [
  "healthScore",
  "staleClaims",
  "highSeverityRisks",
  "atdi",
  "debt",
  "expiredWaivers",
  "unownedEntities",
  "missingAdrCoverage",
] as const;

type HeatmapMetric = (typeof metrics)[number];

export function HomePage({
  domainScope,
  onNavigate,
}: {
  domainScope: string;
  onNavigate: (_route: AppRoute, _params?: Record<string, string>) => void;
}): React.JSX.Element {
  const scopeKey = domainScope || "All domains";
  const summary = useLoad(`dashboard-summary-${scopeKey}`, dashboardClient.summary);
  const heatmap = useLoad(`dashboard-heatmap-${scopeKey}`, dashboardClient.heatmap);
  const tasks = useLoad(`dashboard-review-${scopeKey}`, dashboardClient.reviewTasks);
  const jobs = useLoad(`dashboard-jobs-${scopeKey}`, dashboardClient.recentJobs);
  const events = useLoad(`dashboard-events-${scopeKey}`, dashboardClient.auditEvents);
  const personalFeed = useLoad("home-personal-feed", personalClient.feed);
  const command = useCommand();
  const [metric, setMetric] = useState<HeatmapMetric>("healthScore");

  const sortedHeatmap = useMemo<HeatmapDomain[]>(() => {
    const items = heatmap.data ?? [];
    return [...items].sort((left, right) => (right[metric] ?? 0) - (left[metric] ?? 0));
  }, [heatmap.data, metric]);

  return (
    <section className="page-grid">
      <article className="card">
        <p className="eyebrow">Your space</p>
        <h2>Pick up where you left off</h2>
        <p className="subtle">
          What you have been reading, what you have saved, and what is still waiting for you in the
          domains you can read.
        </p>
        {personalFeed.loading ? <LoadingState label="Loading your space..." /> : null}
        <ErrorState error={personalFeed.error} />
        {personalFeed.data ? (
          <ul className="stack-list" data-testid="home-personal-feed">
            {personalFeed.data.feed.length === 0 ? (
              <li className="subtle">Nothing yet. Open a record and it will appear here.</li>
            ) : null}
            {personalFeed.data.feed.slice(0, 6).map((entry) => (
              <li key={`${entry.targetType}-${entry.targetId}`} data-testid="home-personal-entry">
                <strong>{entry.targetLabel}</strong>
                <span className="subtle">
                  {entry.targetKind} · {entry.reason}
                </span>
              </li>
            ))}
          </ul>
        ) : null}
        {personalFeed.data && personalFeed.data.queue.length > 0 ? (
          <p className="subtle">
            {personalFeed.data.queue.length} record
            {personalFeed.data.queue.length === 1 ? "" : "s"} in your reading queue.
          </p>
        ) : null}
        <div className="actions-row">
          <button type="button" onClick={() => onNavigate("/me")}>
            Open your space
          </button>
        </div>
      </article>
      <article className="card">
        <h2>Knowledge state summary</h2>
        {summary.loading ? <LoadingState label="Loading summary..." /> : null}
        <ErrorState error={summary.error} />
        {summary.data ? (
          <div>
            <p>
              Review tasks {summary.data.reviewTasksAssigned} · stale claims{" "}
              {summary.data.staleClaims} · expired waivers {summary.data.expiredWaivers}
            </p>
            <p>
              Recent ADRs:{" "}
              {summary.data.recentAdrs.length > 0
                ? summary.data.recentAdrs.map((adr) => adr.title).join(" · ")
                : "None"}
            </p>
            <p>
              Top risks:{" "}
              {summary.data.topRisks.length > 0
                ? summary.data.topRisks
                    .map((risk) => `${risk.title} (${risk.severity})`)
                    .join(" · ")
                : "None"}
            </p>
            <p>
              Top debt:{" "}
              {summary.data.topDebt.length > 0
                ? summary.data.topDebt
                    .map((debt) => `${debt.title}${debt.impact ? ` (${debt.impact})` : ""}`)
                    .join(" · ")
                : "None"}
            </p>
          </div>
        ) : null}
      </article>
      <article className="card">
        <h2>Domain health widget</h2>
        <label className="field">
          <span>Metric</span>
          <select
            value={metric}
            onChange={(event) => setMetric(event.target.value as HeatmapMetric)}
          >
            {metrics.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        {heatmap.loading ? <LoadingState label="Loading heatmap..." /> : null}
        <ErrorState error={heatmap.error} />
        {heatmap.data && heatmap.data.length > 0 ? (
          <>
            <Suspense fallback={<LoadingState label="Loading heatmap..." />}>
              <HeatmapChart data={heatmap.data} metric={metric} />
            </Suspense>
            <ul>
              {sortedHeatmap.map((domain) => (
                <li key={domain.id}>
                  {domain.name}: health {domain.healthScore} · stale {domain.staleClaims} · high
                  risk {domain.highSeverityRisks} · ATDI {domain.atdi} · debt {domain.debt}
                </li>
              ))}
            </ul>
          </>
        ) : (
          !heatmap.loading && <EmptyState label="No domain heatmap data." />
        )}
      </article>
      <article className="card">
        <h2>My review tasks widget</h2>
        {tasks.loading ? <LoadingState label="Loading review tasks..." /> : null}
        <ErrorState error={tasks.error} />
        {tasks.data && tasks.data.length > 0
          ? tasks.data.map((task) => (
              <p key={task.id}>
                {task.title} · {task.priority} · due {task.dueDate || "unspecified"}
              </p>
            ))
          : !tasks.loading && <EmptyState label="No review tasks assigned." />}
      </article>
      <article className="card">
        <h2>Ingestion status widget</h2>
        {command.message ? <p role="status">{command.message}</p> : null}
        {jobs.loading ? <LoadingState label="Loading ingestion jobs..." /> : null}
        <ErrorState error={jobs.error} />
        {jobs.data && jobs.data.length > 0
          ? jobs.data.map((job) => (
              <p key={job.id}>
                {job.sourceDocumentId} {job.state}
                <button
                  type="button"
                  disabled={command.busy}
                  onClick={() =>
                    command.runCommand("Retry job", () => dashboardClient.retryJob(job.id))
                  }
                >
                  Retry
                </button>
              </p>
            ))
          : !jobs.loading && <EmptyState label="No recent ingestion jobs." />}
      </article>
      <article className="card">
        <h2>Recent changes widget</h2>
        {events.loading ? <LoadingState label="Loading audit events..." /> : null}
        <ErrorState error={events.error} />
        {events.data && events.data.length > 0
          ? events.data.map((event) => (
              <p key={event.id}>
                {event.actor} {event.action} {event.target} ({event.at})
              </p>
            ))
          : !events.loading && <EmptyState label="No recent changes found." />}
      </article>
      <article className="card">
        <h2>Search / Ask entry point</h2>
        <p>Open Ask Margin to run scoped grounded questions.</p>
      </article>
    </section>
  );
}
