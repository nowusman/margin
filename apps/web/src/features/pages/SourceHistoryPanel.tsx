import React from "react";
import { StatusBadge } from "../../components/badges/Badges";
import { EmptyState, ErrorState, LoadingState } from "../../components/layout/States";
import { DataTable } from "../../components/tables";
import { sourcesClient } from "../../lib/apiAdminSources";
import type { ReprocessingDiff } from "../../lib/apiTypes";
import { shortId } from "../../lib/display";
import { useLoad } from "../../lib/hooks";

type SourceHistoryPanelProps = {
  sourceId: string;
};

const formatDate = (value: string): string => {
  if (!value) return "n/a";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toISOString().slice(0, 19).replace("T", " ") + " GMT";
};

const diffChanges = (diff: ReprocessingDiff): number =>
  diff.addedClaimIds.length +
  diff.removedClaimIds.length +
  diff.modifiedClaims.length +
  diff.changedEntityResolutions.length +
  diff.addedRelationshipIds.length +
  diff.removedRelationshipIds.length +
  diff.newlyUnresolved.length;

export function SourceHistoryPanel({ sourceId }: SourceHistoryPanelProps): React.JSX.Element {
  const history = useLoad(`source-history-${sourceId}`, () => sourcesClient.history(sourceId), {
    enabled: Boolean(sourceId),
  });
  const { data, loading, error } = history;
  return (
    <section className="source-history-panel" aria-labelledby="source-history-title">
      <div className="listview-header">
        <div>
          <h3 id="source-history-title">Source history</h3>
          <p className="subtle">
            Immutable versions, the active page index, and reprocessing changes remain addressable.
          </p>
        </div>
        {data ? (
          <span className="subtle">
            {data.versions.length} version{data.versions.length === 1 ? "" : "s"}
          </span>
        ) : null}
      </div>
      {loading ? <LoadingState label="Loading source history..." /> : null}
      <ErrorState error={error} />
      {data ? (
        <>
          <h4>Version history</h4>
          <DataTable
            rows={data.versions}
            emptyLabel="No source versions recorded."
            caption="Immutable source versions"
            density="compact"
            getRowKey={(row) => row.id}
            columns={[
              {
                header: "Version",
                render: (row) => <span className="technical-id">{shortId(row.id)}</span>,
                sortValue: (row) => row.id,
              },
              {
                header: "Status",
                render: (row) => <StatusBadge label={row.status} />,
                sortValue: (row) => row.status,
              },
              {
                header: "Created",
                render: (row) => formatDate(row.createdAt),
                sortValue: (row) => row.createdAt,
              },
              {
                header: "Converter",
                render: (row) =>
                  [row.converterName, row.converterVersion].filter(Boolean).join(" ") || "n/a",
                sortValue: (row) => row.converterName,
              },
              {
                header: "Hash",
                render: (row) => <span className="technical-id">{shortId(row.versionHash)}</span>,
                sortValue: (row) => row.versionHash,
              },
            ]}
          />

          <h4>Active page index</h4>
          <DataTable
            rows={data.pages}
            emptyLabel="No active source pages recorded."
            caption="Current source pages"
            density="compact"
            getRowKey={(row) => row.id}
            columns={[
              {
                header: "Page",
                render: (row) => row.pageNumber,
                sortValue: (row) => row.pageNumber,
              },
              {
                header: "Page ID",
                render: (row) => <span className="technical-id">{shortId(row.id)}</span>,
                sortValue: (row) => row.id,
              },
              {
                header: "Version",
                render: (row) => (
                  <span className="technical-id">{shortId(row.sourceVersionId)}</span>
                ),
                sortValue: (row) => row.sourceVersionId,
              },
              {
                header: "Checksum",
                render: (row) => (
                  <span className="technical-id">{shortId(row.checksum || "")}</span>
                ),
                sortValue: (row) => row.checksum || "",
              },
            ]}
          />

          <h4>Reprocessing history</h4>
          <DataTable
            rows={data.reprocessingDiffs}
            emptyLabel="No reprocessing differences recorded."
            caption="Version-to-version knowledge changes"
            density="compact"
            getRowKey={(row) => row.id}
            columns={[
              {
                header: "Recorded",
                render: (row) => formatDate(row.createdAt),
                sortValue: (row) => row.createdAt,
              },
              {
                header: "Versions",
                render: (row) =>
                  `${shortId(row.previousVersionId || "initial")} → ${shortId(row.currentVersionId)}`,
                sortValue: (row) => row.currentVersionId,
              },
              {
                header: "Changes",
                render: (row) => diffChanges(row),
                sortValue: diffChanges,
              },
              {
                header: "Claims",
                render: (row) =>
                  `+${row.addedClaimIds.length} / -${row.removedClaimIds.length} / ~${row.modifiedClaims.length}`,
              },
              {
                header: "Relationships",
                render: (row) =>
                  `+${row.addedRelationshipIds.length} / -${row.removedRelationshipIds.length}`,
              },
              {
                header: "Review signals",
                render: (row) =>
                  `${row.changedEntityResolutions.length} resolutions / ${row.newlyUnresolved.length} unresolved`,
              },
            ]}
          />
        </>
      ) : !loading && !error ? (
        <EmptyState label="No source history available." />
      ) : null}
    </section>
  );
}
