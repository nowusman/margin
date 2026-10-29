import React, { useEffect, useState } from "react";
import { StatusBadge } from "../../components/badges/Badges";
import { TextAreaField } from "../../components/forms";
import { EmptyState, ErrorState, LoadingState } from "../../components/layout/States";
import { domainReadingClient } from "../../lib/apiCharter";
import { useCommand, useLoad } from "../../lib/hooks";
import { AppRoute } from "./types";

export type DomainCharterPageProps = {
  route: AppRoute;
  params: Record<string, string>;
  onNavigate: (_route: AppRoute, _params?: Record<string, string>) => void;
};

type Draft = {
  purpose: string;
  scopeInclusions: string;
  scopeExclusions: string;
  terminology: string;
};

const emptyDraft: Draft = {
  purpose: "",
  scopeInclusions: "",
  scopeExclusions: "",
  terminology: "",
};

export function DomainCharterPage({
  route,
  params,
  onNavigate,
}: DomainCharterPageProps): React.JSX.Element {
  const domainId = params.domainId ?? "";
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [activate, setActivate] = useState(true);
  const command = useCommand();

  const charter = useLoad(`charter-${domainId}`, () => domainReadingClient.charter(domainId), {
    enabled: route === "/knowledge/domains/:domainId/charter" && Boolean(domainId),
  });

  useEffect(() => {
    // The editor shows the newest authored version, so a draft is not lost
    // behind the version that is in force.
    const newest = charter.data?.history?.[0] ?? charter.data?.active;
    if (!newest) return;
    setDraft({
      purpose: newest.purpose,
      scopeInclusions: newest.scopeInclusions,
      scopeExclusions: newest.scopeExclusions,
      terminology: newest.terminology,
    });
  }, [charter.data]);

  if (!domainId) {
    return <EmptyState label="Choose a domain before authoring its charter." />;
  }
  if (charter.loading) return <LoadingState label="Loading the domain charter..." />;
  if (charter.error) return <ErrorState error={charter.error} />;

  const active = charter.data?.active ?? null;
  const history = charter.data?.history ?? [];

  return (
    <section className="page-grid">
      <article className="card">
        <p className="eyebrow">Domain charter</p>
        <h2>What this domain covers</h2>
        <p className="subtle">
          A charter tells the extractor what this domain is for, what belongs inside it and what
          does not. It guides extraction. It never influences ranking, and it never becomes a claim
          on its own.
        </p>
        {active ? (
          <p className="subtle">
            Version {active.version} is in force, authored by{" "}
            {active.authorPrincipalId || "unknown"}.
          </p>
        ) : (
          <p className="subtle">This domain has no charter in force yet.</p>
        )}
      </article>

      <article className="card">
        <h3>Author a new version</h3>
        <div className="stacked-form">
          <TextAreaField
            label="Purpose"
            rows={3}
            value={draft.purpose}
            onChange={(purpose) => setDraft((current) => ({ ...current, purpose }))}
            placeholder="What this domain is responsible for."
          />
          <TextAreaField
            label="In scope"
            rows={3}
            value={draft.scopeInclusions}
            onChange={(scopeInclusions) => setDraft((current) => ({ ...current, scopeInclusions }))}
            placeholder="What belongs here."
          />
          <TextAreaField
            label="Out of scope"
            rows={3}
            value={draft.scopeExclusions}
            onChange={(scopeExclusions) => setDraft((current) => ({ ...current, scopeExclusions }))}
            placeholder="What deliberately does not belong here."
          />
          <TextAreaField
            label="Preferred terminology"
            rows={3}
            value={draft.terminology}
            onChange={(terminology) => setDraft((current) => ({ ...current, terminology }))}
            placeholder="The words this domain uses instead of others."
          />
          <label className="field">
            <span>Put this version into force</span>
            <input
              type="checkbox"
              checked={activate}
              onChange={(event) => setActivate(event.target.checked)}
            />
          </label>
          <div className="actions-row">
            <button
              type="button"
              onClick={() =>
                command.runCommand("Save charter", () =>
                  domainReadingClient
                    .authorCharter(domainId, {
                      purpose: draft.purpose,
                      scopeInclusions: draft.scopeInclusions,
                      scopeExclusions: draft.scopeExclusions,
                      terminology: draft.terminology,
                      activate,
                    })
                    .then(() => {
                      charter.refresh();
                    })
                )
              }
            >
              Save charter
            </button>
            <button type="button" onClick={() => onNavigate("/architecture/wiki")}>
              Read the domain
            </button>
          </div>
        </div>
        {command.message ? <p className="subtle">{command.message}</p> : null}
      </article>

      <article className="card">
        <h3>Version history</h3>
        {history.length === 0 ? (
          <EmptyState label="No charter has been authored for this domain." />
        ) : (
          <ul className="stack-list" data-testid="charter-history">
            {history.map((entry) => (
              <li key={entry.id} data-testid="charter-version">
                <strong>Version {entry.version}</strong>
                <StatusBadge label={entry.status} />
                <span className="subtle">{entry.purpose || "No purpose recorded."}</span>
                <span className="subtle">Authored by {entry.authorPrincipalId || "unknown"}</span>
              </li>
            ))}
          </ul>
        )}
      </article>
    </section>
  );
}
