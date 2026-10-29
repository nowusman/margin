import React, { useMemo, useState } from "react";
import { StatusBadge } from "../../components/badges/Badges";
import { EmptyState, ErrorState, LoadingState } from "../../components/layout/States";
import { MarkdownContent } from "../../components/rich/MarkdownContent";
import { DataTable } from "../../components/tables";
import { domainReadingClient } from "../../lib/apiCharter";
import { governanceClient } from "../../lib/apiGovernance";
import { knowledgeClient } from "../../lib/apiKnowledge";
import { shortId } from "../../lib/display";
import { useLoad } from "../../lib/hooks";
import { typeLabel } from "./GovernancePage.helpers";
import { AppRoute } from "./types";

export type ArchitectureWikiPageProps = {
  onNavigate: (_route: AppRoute, _params?: Record<string, string>) => void;
};

const excerpt = (value: string, limit = 180): string =>
  value.length > limit ? `${value.slice(0, limit - 1)}…` : value;

export function ArchitectureWikiPage({ onNavigate }: ArchitectureWikiPageProps): React.JSX.Element {
  const [generated, setGenerated] = useState(false);
  const [selectedDomainId, setSelectedDomainId] = useState("");
  const domains = useLoad("wiki-domains", () => knowledgeClient.listDomains("read"), {
    enabled: generated,
  });
  const entities = useLoad("wiki-entities", knowledgeClient.listEntities, { enabled: generated });
  const ideaBlocks = useLoad("wiki-ideablocks", knowledgeClient.listIdeaBlocks, {
    enabled: generated,
  });
  const records = useLoad("wiki-governance-records", () => governanceClient.records(), {
    enabled: generated,
  });
  const c4Tree = useLoad("wiki-c4-tree", knowledgeClient.c4Tree, { enabled: generated });
  const overlays = useLoad("wiki-c4-overlays", knowledgeClient.c4Overlays, { enabled: generated });
  const diagrams = useLoad("wiki-diagrams", knowledgeClient.diagrams, { enabled: generated });

  const domainOptions = useMemo(() => domains.data ?? [], [domains.data]);
  const activeDomainId = selectedDomainId || domainOptions[0]?.id || "";
  const activeDomainName = useMemo(
    () => domainOptions.find((domain) => domain.id === activeDomainId)?.name ?? "",
    [domainOptions, activeDomainId]
  );
  // The tables describe the domain the reader is reading, so they follow it.
  const domainEntities = useMemo(
    () => (entities.data ?? []).filter((entity) => entity.domainId === activeDomainId),
    [entities.data, activeDomainId]
  );
  const domainIdeaBlocks = useMemo(
    () => (ideaBlocks.data ?? []).filter((block) => block.domainId === activeDomainId),
    [ideaBlocks.data, activeDomainId]
  );
  const domainRecords = useMemo(
    () => (records.data ?? []).filter((record) => record.domainId === activeDomainId),
    [records.data, activeDomainId]
  );
  const narrative = useLoad(
    `wiki-narrative-${activeDomainId || "none"}`,
    () => domainReadingClient.narrative(activeDomainId),
    { enabled: generated && Boolean(activeDomainId) }
  );
  const knownUnknowns = useLoad(
    `wiki-known-unknowns-${activeDomainId || "none"}`,
    () => domainReadingClient.knownUnknowns(activeDomainId),
    { enabled: generated && Boolean(activeDomainId) }
  );
  const briefing = useLoad(
    `wiki-briefing-${activeDomainId || "none"}`,
    () => domainReadingClient.briefing(activeDomainId),
    { enabled: generated && Boolean(activeDomainId) }
  );

  const refreshAll = (): void => {
    [domains, entities, ideaBlocks, records, c4Tree, overlays, diagrams].forEach((resource) =>
      resource.refresh()
    );
    narrative.refresh();
    knownUnknowns.refresh();
    briefing.refresh();
  };
  const generate = (): void => {
    if (generated) {
      refreshAll();
      return;
    }
    setGenerated(true);
  };

  return (
    <section className="page-grid">
      <article className="card">
        <p className="eyebrow">Architecture Wiki</p>
        <h2>Generate the current architecture view</h2>
        <p className="subtle">
          Build an on-demand, read-only view from the latest authorized domains, knowledge,
          governance records, C4 data, and diagrams. Nothing is compiled or stored as a second copy.
        </p>
        <div className="actions-row">
          <button type="button" onClick={generate}>
            {generated ? "Regenerate wiki" : "Generate architecture wiki"}
          </button>
        </div>
      </article>

      {!generated ? (
        <EmptyState label="Generate the wiki when you need a fresh authorized architecture snapshot." />
      ) : (
        <>
          <article className="card">
            <p className="eyebrow">Generated overview</p>
            <h2>Authorized architecture snapshot</h2>
            <p>
              {domains.data?.length ?? 0} domains · {entities.data?.length ?? 0} entities ·{" "}
              {ideaBlocks.data?.length ?? 0} Idea Blocks · {records.data?.length ?? 0} governance
              records
            </p>
            <p className="subtle">Refresh the wiki to read the latest canonical records.</p>
          </article>

          <article className="card">
            <p className="eyebrow">
              Narrative view{activeDomainName ? ` · ${activeDomainName}` : ""}
            </p>
            <h2>Read the domain</h2>
            <p className="subtle">
              The narrative is composed from the canonical records you may read. It is generated on
              request and never stored, so it cannot drift from the records it describes.
            </p>
            {domainOptions.length > 0 ? (
              <div className="actions-row" role="group" aria-label="Choose a domain to read">
                {domainOptions.map((domain) => (
                  <button
                    key={domain.id}
                    type="button"
                    aria-pressed={domain.id === activeDomainId}
                    onClick={() => setSelectedDomainId(domain.id)}
                  >
                    {domain.name}
                  </button>
                ))}
              </div>
            ) : null}
            {narrative.loading ? <LoadingState label="Composing the domain narrative..." /> : null}
            <ErrorState error={narrative.error} />
            {briefing.data ? (
              <p className="subtle" data-testid="wiki-briefing">
                {briefing.data.briefing}
              </p>
            ) : null}
            {narrative.data ? (
              <div data-testid="wiki-narrative">
                <MarkdownContent content={narrative.data.markdown} />
                <p className="subtle">
                  {narrative.data.entityCount} record
                  {narrative.data.entityCount === 1 ? "" : "s"} · {narrative.data.claimCount}{" "}
                  verified claim
                  {narrative.data.claimCount === 1 ? "" : "s"} · {narrative.data.inferredClaimCount}{" "}
                  claim awaiting verification
                  {narrative.data.inferredClaimCount === 1 ? "" : "s"} ·{" "}
                  {narrative.data.decisionCount} decision
                  {narrative.data.decisionCount === 1 ? "" : "s"}
                </p>
                <div className="actions-row">
                  <button
                    type="button"
                    onClick={() =>
                      onNavigate("/knowledge/domains/:domainId/charter", {
                        domainId: activeDomainId,
                      })
                    }
                  >
                    Edit the domain charter
                  </button>
                </div>
              </div>
            ) : null}
            {knownUnknowns.data && knownUnknowns.data.length > 0 ? (
              <div className="callout" data-testid="wiki-known-unknowns">
                <h3>Referenced but not yet recorded</h3>
                <ul className="stack-list">
                  {knownUnknowns.data.map((known) => (
                    <li key={known.placeholderId} data-testid="wiki-known-unknown">
                      <strong>{known.text}</strong>
                      <span className="subtle">
                        referenced {known.referenceCount} time
                        {known.referenceCount === 1 ? "" : "s"} · {known.proposedType}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </article>

          <article className="card">
            <h2>Domains</h2>
            {domains.loading ? <LoadingState label="Loading authorized domains..." /> : null}
            <ErrorState error={domains.error} />
            {domains.data && domains.data.length > 0 ? (
              <DataTable
                rows={domains.data}
                emptyLabel="No authorized domains available."
                caption="Architecture domains"
                density="compact"
                getRowKey={(row) => row.id}
                columns={[
                  {
                    header: "Domain",
                    render: (row) => (
                      <button
                        type="button"
                        className="table-link"
                        onClick={() =>
                          onNavigate("/knowledge/domains/:domainId", { domainId: row.id })
                        }
                      >
                        {row.name}
                      </button>
                    ),
                    sortValue: (row) => row.name,
                  },
                  { header: "Owner", render: (row) => row.owner || "Unassigned" },
                  { header: "Steward", render: (row) => row.steward || "Unassigned" },
                  {
                    header: "Status",
                    render: (row) => <StatusBadge label={row.status} />,
                    sortValue: (row) => row.status,
                  },
                ]}
              />
            ) : !domains.loading && !domains.error ? (
              <EmptyState label="No authorized domains available." />
            ) : null}
          </article>

          <article className="card">
            <h2>Knowledge</h2>
            {entities.loading ? <LoadingState label="Loading authorized entities..." /> : null}
            <ErrorState error={entities.error} />
            {domainEntities.length > 0 ? (
              <DataTable
                rows={domainEntities}
                emptyLabel="No authorized entities available."
                caption="Canonical entities"
                density="compact"
                getRowKey={(row) => row.id}
                columns={[
                  {
                    header: "Entity",
                    render: (row) => (
                      <button
                        type="button"
                        className="table-link"
                        onClick={() =>
                          onNavigate("/knowledge/entities/:entityId", { entityId: row.id })
                        }
                      >
                        {row.name}
                      </button>
                    ),
                    sortValue: (row) => row.name,
                  },
                  { header: "Type", render: (row) => row.kind, sortValue: (row) => row.kind },
                  { header: "Domain", render: (row) => row.domainId || "Unassigned" },
                  {
                    header: "Status",
                    render: (row) => <StatusBadge label={row.status} />,
                    sortValue: (row) => row.status,
                  },
                ]}
              />
            ) : !entities.loading && !entities.error ? (
              <EmptyState label="No authorized entities available." />
            ) : null}

            {ideaBlocks.loading ? <LoadingState label="Loading authorized Idea Blocks..." /> : null}
            <ErrorState error={ideaBlocks.error} />
            {domainIdeaBlocks.length > 0 ? (
              <DataTable
                rows={domainIdeaBlocks}
                emptyLabel="No authorized Idea Blocks available."
                caption="Current Idea Blocks"
                density="compact"
                getRowKey={(row) => row.id}
                columns={[
                  {
                    header: "Statement",
                    render: (row) => (
                      <button
                        type="button"
                        className="table-link"
                        onClick={() =>
                          onNavigate("/knowledge/ideablocks/:ideaBlockId", { ideaBlockId: row.id })
                        }
                      >
                        {excerpt(row.statement || `Idea Block ${shortId(row.id)}`)}
                      </button>
                    ),
                    sortValue: (row) => row.statement,
                  },
                  { header: "Type", render: (row) => row.type || "Unclassified" },
                  {
                    header: "Publication",
                    render: (row) => <StatusBadge label={row.publicationStatus} />,
                    sortValue: (row) => row.publicationStatus,
                  },
                ]}
              />
            ) : !ideaBlocks.loading && !ideaBlocks.error ? (
              <EmptyState label="No authorized Idea Blocks available." />
            ) : null}
          </article>

          <article className="card">
            <h2>Governance</h2>
            <p className="subtle">
              ADR, risk, waiver, technical-debt, and Architecture Backlog records share one governed
              aggregate. Open an ADR to inspect its immutable revision history.
            </p>
            {records.loading ? (
              <LoadingState label="Loading authorized governance records..." />
            ) : null}
            <ErrorState error={records.error} />
            {domainRecords.length > 0 ? (
              <DataTable
                rows={domainRecords}
                emptyLabel="No authorized governance records available."
                caption="Governance records"
                density="compact"
                getRowKey={(row) => row.id}
                columns={[
                  {
                    header: "Record",
                    render: (row) => (
                      <button
                        type="button"
                        className="table-link"
                        onClick={() =>
                          onNavigate("/governance/records/:recordId", { recordId: row.id })
                        }
                      >
                        {row.title || row.businessKey || shortId(row.id)}
                      </button>
                    ),
                    sortValue: (row) => row.title,
                  },
                  { header: "Type", render: (row) => typeLabel(row.recordType) },
                  {
                    header: "Lifecycle",
                    render: (row) => <StatusBadge label={row.lifecycleStatus} />,
                    sortValue: (row) => row.lifecycleStatus,
                  },
                  { header: "Domain", render: (row) => row.domainId || "Unassigned" },
                ]}
              />
            ) : !records.loading && !records.error ? (
              <EmptyState label="No authorized governance records available." />
            ) : null}
          </article>

          <article className="card">
            <h2>Architecture views and relationships</h2>
            <p className="subtle">
              C4 nodes and overlays are read from the current catalog. Relationships remain bounded
              and evidence-backed in their dedicated explorer.
            </p>
            {c4Tree.loading ? <LoadingState label="Loading C4 structure..." /> : null}
            <ErrorState error={c4Tree.error} />
            {overlays.loading ? <LoadingState label="Loading architecture overlays..." /> : null}
            <ErrorState error={overlays.error} />
            {diagrams.loading ? <LoadingState label="Loading governed diagrams..." /> : null}
            <ErrorState error={diagrams.error} />
            <p>
              C4 nodes: {c4Tree.data?.length ?? 0} · overlays: {overlays.data?.length ?? 0} ·
              diagrams: {diagrams.data?.length ?? 0}
            </p>
            <div className="actions-row">
              <button type="button" onClick={() => onNavigate("/views/heatmap")}>
                Open architecture heatmap
              </button>
              <button type="button" onClick={() => onNavigate("/views/c4")}>
                Open C4 explorer
              </button>
              <button type="button" onClick={() => onNavigate("/knowledge/relationships")}>
                Open relationship explorer
              </button>
            </div>
            {!c4Tree.loading &&
            !c4Tree.error &&
            !overlays.loading &&
            !overlays.error &&
            !diagrams.loading &&
            !diagrams.error &&
            !c4Tree.data?.length &&
            !overlays.data?.length &&
            !diagrams.data?.length ? (
              <EmptyState label="No architecture view records are currently available." />
            ) : null}
          </article>
        </>
      )}
    </section>
  );
}
