import React, { useMemo, useState } from "react";
import { assistantClient } from "../../lib/apiRetrieval";
import type { SearchRequest } from "../../lib/apiTypes";
import { useLoad } from "../../lib/hooks";
import { AppRoute } from "../../features/pages/types";

type SearchRecord = {
  id: string;
  type: "Idea Block" | "Entity" | "Governance Record" | "Source Element";
  label: string;
  route: AppRoute;
  params?: Record<string, string>;
};

type GlobalSearchCommandProps = {
  onNavigate: (_route: AppRoute, _params?: Record<string, string>) => void;
};

const searchRequest = (query: string): SearchRequest => ({
  query,
  filters: {
    domainIds: [],
    categoryIds: [],
    entityIds: [],
    entityTerms: [],
    entityTypes: [],
    relationshipTypes: [],
    ideaBlockTypes: [],
    sourceDocumentIds: [],
    sensitivities: [],
    statuses: ["verified"],
    validFrom: null,
    validTo: null,
    governanceRecordTypes: [],
    governanceStatuses: [],
    architectureBacklogStates: [],
  },
  topK: 8,
  expand: { relationTypes: [], depth: 0, limit: 0 },
});

export function GlobalSearchCommand({ onNavigate }: GlobalSearchCommandProps): React.JSX.Element {
  const [query, setQuery] = useState("");
  const trimmedQuery = query.trim();
  const search = useLoad(
    `global-search-${trimmedQuery}`,
    (signal) => assistantClient.search(searchRequest(trimmedQuery), signal),
    { enabled: Boolean(trimmedQuery) }
  );

  const records = useMemo<SearchRecord[]>(() => {
    const byId = new Map<string, SearchRecord>();
    for (const result of search.data?.results ?? []) {
      if (result.ideaBlockId) {
        byId.set(`idea-block:${result.ideaBlockId}`, {
          id: result.ideaBlockId,
          type: "Idea Block",
          label: result.statement || result.ideaBlockId,
          route: "/knowledge/ideablocks/:ideaBlockId",
          params: { ideaBlockId: result.ideaBlockId },
        });
      }
      for (const entity of result.entities) {
        if (!entity.id) continue;
        byId.set(`entity:${entity.id}`, {
          id: entity.id,
          type: "Entity",
          label: entity.name || entity.id,
          route: "/knowledge/entities/:entityId",
          params: { entityId: entity.id },
        });
      }
      for (const record of result.governanceRecords) {
        if (!record.id) continue;
        byId.set(`governance:${record.id}`, {
          id: record.id,
          type: "Governance Record",
          label: record.title || record.id,
          route: "/governance/records/:recordId",
          params: { recordId: record.id },
        });
      }
      for (const citation of result.citations) {
        if (!citation.sourceDocumentId || !citation.sourceElementId) continue;
        byId.set(`source-element:${citation.sourceElementId}`, {
          id: citation.sourceElementId,
          type: "Source Element",
          label: citation.sourceTitle || citation.sourceElementId,
          route: "/sources/:sourceId/elements/:elementId",
          params: {
            sourceId: citation.sourceDocumentId,
            elementId: citation.sourceElementId,
          },
        });
      }
    }
    return Array.from(byId.values()).slice(0, 10);
  }, [search.data?.results]);

  return (
    <div className="search-command">
      <span className="search-command-icon" aria-hidden="true">
        Search
      </span>
      <input
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="Search authorized knowledge"
        aria-label="Global search"
        aria-controls="global-search-results"
        aria-expanded={Boolean(trimmedQuery)}
      />
      {trimmedQuery ? (
        <ul id="global-search-results" aria-live="polite">
          {search.loading ? <li className="subtle">Searching authorized knowledge…</li> : null}
          {search.error ? (
            <li className="state-error">Search unavailable: {search.error}</li>
          ) : null}
          {!search.loading && !search.error && records.length === 0 ? (
            <li className="subtle">No authorized results found.</li>
          ) : null}
          {records.map((record) => (
            <li key={`${record.type}-${record.id}`}>
              <button type="button" onClick={() => onNavigate(record.route, record.params)}>
                <strong>{record.type}</strong>
                <span>{record.label}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
