import React, { useEffect, useMemo, useState } from "react";
import { AssistantStateNotice } from "../../components/assistant/AssistantStateNotice";
import { CitationInline } from "../../components/citations";
import { MarkdownContent } from "../../components/rich/MarkdownContent";
import { EvidenceCard, EvidenceCardData } from "../../components/evidence/EvidenceCard";
import { TextAreaField } from "../../components/forms";
import { CollapsibleCard } from "../../components/layout/CollapsibleCard";
import { EmptyState, ErrorState, LoadingState } from "../../components/layout/States";
import { sourcesClient } from "../../lib/apiAdminSources";
import { knowledgeClient } from "../../lib/apiKnowledge";
import { assistantClient } from "../../lib/apiRetrieval";
import type {
  AskRequest,
  RetrievalCitation,
  RetrievalFilters,
  RetrievalResultCard,
} from "../../lib/apiTypes";
import { useLoad } from "../../lib/hooks";
import { personalClient } from "../../lib/apiPersonal";
import { AppRoute } from "./types";

export type AskPageProps = {
  params?: Record<string, string>;
  query?: Record<string, string>;
  onNavigate: (_route: AppRoute, _params?: Record<string, string>) => void;
};

type ArrayFilterKey = {
  [Key in keyof RetrievalFilters]: RetrievalFilters[Key] extends string[] ? Key : never;
}[keyof RetrievalFilters];

type InlineAnswerPart = { kind: "text"; value: string } | { kind: "citation"; citationId: string };

const INLINE_CITATION_PATTERN = /\[([a-zA-Z0-9:_-]+)\]/g;

const emptyFilters = (): RetrievalFilters => ({
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
});

const defaultRequest = (): AskRequest => ({
  query: "What high severity risks have missing ADR coverage?",
  filters: emptyFilters(),
  topK: 8,
  expand: { relationTypes: [], depth: 0, limit: 0 },
});

const citationLabel = (citation: RetrievalCitation): string => {
  if (citation.redacted) return "Restricted evidence";
  return (
    [citation.sourceTitle, citation.pageNumber === null ? "" : `p.${citation.pageNumber}`]
      .filter(Boolean)
      .join(" - ") || "Evidence citation"
  );
};

const formatFilter = (label: string, values: string[]): string | null =>
  values.length > 0 ? `${label}: ${values.join(", ")}` : null;

export function splitAnswerInlineCitations(answer: string): InlineAnswerPart[] {
  if (!answer) return [];
  const parts: InlineAnswerPart[] = [];
  let lastIndex = 0;
  INLINE_CITATION_PATTERN.lastIndex = 0;
  for (const match of answer.matchAll(INLINE_CITATION_PATTERN)) {
    const start = match.index ?? 0;
    if (start > lastIndex) parts.push({ kind: "text", value: answer.slice(lastIndex, start) });
    parts.push({ kind: "citation", citationId: match[1] });
    lastIndex = start + match[0].length;
  }
  if (lastIndex < answer.length) parts.push({ kind: "text", value: answer.slice(lastIndex) });
  return parts.length ? parts : [{ kind: "text", value: answer }];
}

export function AskPage({ params, query, onNavigate }: AskPageProps): React.JSX.Element {
  const [draft, setDraft] = useState<AskRequest>(defaultRequest);
  const [request, setRequest] = useState<{ id: number; payload: AskRequest } | null>(null);
  const [selectedCitationId, setSelectedCitationId] = useState<string | null>(null);
  const [outputLanguage, setOutputLanguage] = useState("");
  const threadId = params?.threadId ?? "";
  const preparedQuestion = query?.q ?? "";
  const thread = useLoad(
    threadId ? `ask-thread-${threadId}` : "ask-thread-none",
    () => personalClient.thread(threadId),
    { enabled: Boolean(threadId) }
  );
  const answer = useLoad(
    request ? `ask-${request.id}` : "ask-none",
    (signal) => assistantClient.ask(request?.payload ?? defaultRequest(), signal),
    { enabled: Boolean(request) }
  );
  const domains = useLoad("ask-domains", () => knowledgeClient.listDomains("read"));
  const sources = useLoad("ask-sources", sourcesClient.list);
  const filterOptions = useLoad("ask-filter-options", assistantClient.filterOptions);

  // The exchange is appended on the server, so the earlier turns are reloaded
  // once the answer has arrived.
  useEffect(() => {
    if (!answer.data || !threadId) return;
    thread.refresh();
  }, [answer.data, threadId, thread.refresh]);

  // A question prepared elsewhere, such as the investigation a knowledge gap
  // proposes, arrives with the route and becomes the draft to ask.
  useEffect(() => {
    if (!preparedQuestion) return;
    setDraft((current) =>
      current.query === preparedQuestion ? current : { ...current, query: preparedQuestion }
    );
  }, [preparedQuestion]);

  const setSingleFilter = (key: ArrayFilterKey, value: string): void => {
    setDraft((current) => ({
      ...current,
      filters: { ...current.filters, [key]: value ? [value] : [] },
    }));
  };
  const setFilterDate = (key: "validFrom" | "validTo", value: string): void => {
    setDraft((current) => ({
      ...current,
      filters: { ...current.filters, [key]: value || null },
    }));
  };
  const setEntityTerm = (value: string): void => setSingleFilter("entityTerms", value.trim());
  const setExpansion = (key: "relationTypes" | "depth", value: string): void => {
    setDraft((current) => ({
      ...current,
      expand:
        key === "depth"
          ? value === "0"
            ? { relationTypes: [], depth: 0, limit: 0 }
            : { ...current.expand, depth: Number(value) || 1, limit: 8 }
          : value
            ? {
                ...current.expand,
                relationTypes: [value],
                depth: current.expand.depth || 1,
                limit: current.expand.limit || 8,
              }
            : { relationTypes: [], depth: 0, limit: 0 },
    }));
  };

  const citations = useMemo(
    () =>
      [...(answer.data?.results ?? []), ...(answer.data?.expansion.results ?? [])].flatMap(
        (result) => result.citations
      ),
    [answer.data?.expansion.results, answer.data?.results]
  );
  const citationById = useMemo(
    () => new Map(citations.map((citation) => [citation.id, citation])),
    [citations]
  );
  const selectedCitation = citationById.get(selectedCitationId ?? "") ?? null;
  const evidence = useLoad(
    `evidence-${selectedCitation?.ideaBlockId || "none"}`,
    (signal) => assistantClient.evidence(selectedCitation?.ideaBlockId ?? "", signal),
    { enabled: Boolean(selectedCitation?.ideaBlockId && !selectedCitation.redacted) }
  );
  const inlineAnswerParts = useMemo(
    () => splitAnswerInlineCitations(answer.data?.answer ?? ""),
    [answer.data?.answer]
  );
  const activeFilters = useMemo(() => {
    const filters = answer.data?.appliedFilters;
    if (!filters) return [];
    return [
      formatFilter("Domains", filters.domainIds),
      formatFilter("Categories", filters.categoryIds),
      formatFilter("Entities", filters.entityIds.length ? filters.entityIds : filters.entityTerms),
      formatFilter("Entity types", filters.entityTypes),
      formatFilter("Idea Block types", filters.ideaBlockTypes),
      formatFilter("Relationship types", filters.relationshipTypes),
      formatFilter("Sources", filters.sourceDocumentIds),
      formatFilter("Sensitivity", filters.sensitivities),
      formatFilter("Knowledge status", filters.statuses),
      formatFilter("Governance", filters.governanceRecordTypes),
      formatFilter("Governance status", filters.governanceStatuses),
      formatFilter("Architecture Backlog", filters.architectureBacklogStates),
      filters.validFrom ? `Valid from: ${filters.validFrom}` : null,
      filters.validTo ? `Valid to: ${filters.validTo}` : null,
    ].filter((value): value is string => Boolean(value));
  }, [answer.data?.appliedFilters]);
  const evidenceCard = useMemo<EvidenceCardData | null>(() => {
    if (!selectedCitation) return null;
    return {
      sourceTitle: selectedCitation.sourceTitle,
      sourceType: selectedCitation.elementType || "document",
      version: selectedCitation.sourceVersionId,
      pageLabel: selectedCitation.sourcePageId,
      elementLabel: selectedCitation.sourceElementId,
      quote: selectedCitation.quote,
      ideaBlockId: selectedCitation.ideaBlockId,
      confidence: selectedCitation.confidence,
      verificationStatus: "verified",
      freshness: selectedCitation.validTo ? "time-bound" : "current",
      sensitivity: draft.filters.sensitivities[0] || "permitted",
      permissionsState: selectedCitation.redacted ? "Restricted" : "Full access",
      redacted: selectedCitation.redacted,
      redactionReason: selectedCitation.redactionReason,
      pageCoordinates:
        selectedCitation.pageNumber === null ? "Not provided" : `p.${selectedCitation.pageNumber}`,
    };
  }, [draft.filters.sensitivities, selectedCitation]);
  const governanceStatuses = useMemo(() => {
    const recordType = draft.filters.governanceRecordTypes[0];
    return recordType ? (filterOptions.data?.governanceStatuses[recordType] ?? []) : [];
  }, [draft.filters.governanceRecordTypes, filterOptions.data?.governanceStatuses]);

  const renderResult = (result: RetrievalResultCard): React.JSX.Element => (
    <article className="card" key={result.ideaBlockRevisionId || result.ideaBlockId}>
      <div className="listview-header">
        <div>
          <p className="eyebrow">{result.type || "Idea Block"}</p>
          <h3>{result.statement || "Untitled Idea Block"}</h3>
        </div>
        <span className="badge">{result.status || "unknown"}</span>
      </div>
      <p className="subtle">
        Score {result.score.toFixed(3)} · {result.polarity || "supporting"}
      </p>
      {result.snippet && result.snippet !== result.statement ? (
        <p className="subtle" data-testid="ask-result-snippet">
          {result.snippet}
        </p>
      ) : null}
      {result.related.length > 0 ? (
        <p className="subtle" data-testid="ask-result-related">
          Related: {result.related.map((record) => record.label).join(", ")}
        </p>
      ) : null}
      {result.entities.length > 0 ? (
        <p>Entities: {result.entities.map((entity) => entity.name).join(", ")}</p>
      ) : null}
      {result.contradictions.length > 0 ? (
        <p role="status">Contradictions: {result.contradictions.length}</p>
      ) : null}
      {result.supersession.length > 0 ? (
        <p className="subtle">Supersession context: {result.supersession.length}</p>
      ) : null}
      {result.governanceRecords.length > 0 ? (
        <ul aria-label="Linked governance records">
          {result.governanceRecords.map((record) => (
            <li key={record.id}>
              <button
                type="button"
                onClick={() => onNavigate("/governance/records/:recordId", { recordId: record.id })}
              >
                {record.type}: {record.title || record.id} ({record.status})
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="citation-grid" aria-label="Result citations">
        {result.citations.map((citation) => (
          <CitationInline
            key={citation.id}
            citationId={citation.id}
            label={citationLabel(citation)}
            active={citation.id === selectedCitationId}
            disabled={citation.redacted}
            onClick={() => setSelectedCitationId(citation.id)}
          />
        ))}
      </div>
      <button
        type="button"
        onClick={() =>
          onNavigate("/knowledge/ideablocks/:ideaBlockId", { ideaBlockId: result.ideaBlockId })
        }
        disabled={!result.ideaBlockId}
      >
        Open IdeaBlock
      </button>
    </article>
  );

  return (
    <section className="ask-layout">
      <CollapsibleCard title="Search scope" className="ask-scope-panel">
        {filterOptions.loading ? <LoadingState label="Loading governed filter options..." /> : null}
        <ErrorState error={filterOptions.error} />
        <div className="filter-grid ask-scope-grid">
          <label className="field compact-field">
            <span>Domain</span>
            <select
              value={draft.filters.domainIds[0] ?? ""}
              onChange={(event) => setSingleFilter("domainIds", event.target.value)}
            >
              <option value="">All permitted domains</option>
              {(domains.data ?? []).map((domain) => (
                <option key={domain.id} value={domain.id}>
                  {domain.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field compact-field">
            <span>Source category</span>
            <select
              value={draft.filters.categoryIds[0] ?? ""}
              onChange={(event) => setSingleFilter("categoryIds", event.target.value)}
            >
              <option value="">All permitted categories</option>
              {(filterOptions.data?.categories ?? []).map((category) => (
                <option key={category.id} value={category.id}>
                  {category.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field compact-field">
            <span>Sensitivity</span>
            <select
              value={draft.filters.sensitivities[0] ?? ""}
              onChange={(event) => setSingleFilter("sensitivities", event.target.value)}
            >
              <option value="">All permitted sensitivities</option>
              {(filterOptions.data?.sensitivities ?? []).map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="field compact-field">
            <span>Knowledge status</span>
            <select
              value={draft.filters.statuses[0] ?? "verified"}
              onChange={(event) => setSingleFilter("statuses", event.target.value)}
            >
              {(filterOptions.data?.ideaBlockStatuses ?? ["verified"]).map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="field compact-field">
            <span>Entity or alias</span>
            <input
              value={draft.filters.entityTerms[0] ?? ""}
              onChange={(event) => setEntityTerm(event.target.value)}
              placeholder="Resolve through the Entity Catalog"
            />
          </label>
          <label className="field compact-field">
            <span>Entity type</span>
            <select
              value={draft.filters.entityTypes[0] ?? ""}
              onChange={(event) => setSingleFilter("entityTypes", event.target.value)}
            >
              <option value="">Any type</option>
              {(filterOptions.data?.entityTypes ?? []).map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="field compact-field">
            <span>Idea Block type</span>
            <select
              value={draft.filters.ideaBlockTypes[0] ?? ""}
              onChange={(event) => setSingleFilter("ideaBlockTypes", event.target.value)}
            >
              <option value="">Any type</option>
              {(filterOptions.data?.ideaBlockTypes ?? []).map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="field compact-field">
            <span>Source</span>
            <select
              value={draft.filters.sourceDocumentIds[0] ?? ""}
              onChange={(event) => setSingleFilter("sourceDocumentIds", event.target.value)}
            >
              <option value="">All permitted sources</option>
              {(sources.data ?? []).map((source) => (
                <option key={source.id} value={source.id}>
                  {source.title}
                </option>
              ))}
            </select>
          </label>
          <label className="field compact-field">
            <span>Governance type</span>
            <select
              value={draft.filters.governanceRecordTypes[0] ?? ""}
              onChange={(event) => setSingleFilter("governanceRecordTypes", event.target.value)}
            >
              <option value="">Any governance type</option>
              {(filterOptions.data?.governanceRecordTypes ?? []).map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="field compact-field">
            <span>Governance status</span>
            <select
              value={draft.filters.governanceStatuses[0] ?? ""}
              onChange={(event) => setSingleFilter("governanceStatuses", event.target.value)}
              disabled={governanceStatuses.length === 0}
            >
              <option value="">Any status</option>
              {governanceStatuses.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          {draft.filters.governanceRecordTypes[0] === "architecture_backlog" ? (
            <label className="field compact-field">
              <span>Architecture Backlog state</span>
              <select
                value={draft.filters.architectureBacklogStates[0] ?? ""}
                onChange={(event) =>
                  setSingleFilter("architectureBacklogStates", event.target.value)
                }
              >
                <option value="">Any state</option>
                {(filterOptions.data?.architectureBacklogStates ?? []).map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <label className="field compact-field">
            <span>Valid from</span>
            <input
              type="date"
              value={draft.filters.validFrom ?? ""}
              onChange={(event) => setFilterDate("validFrom", event.target.value)}
            />
          </label>
          <label className="field compact-field">
            <span>Valid to</span>
            <input
              type="date"
              value={draft.filters.validTo ?? ""}
              onChange={(event) => setFilterDate("validTo", event.target.value)}
            />
          </label>
          <label className="field compact-field">
            <span>Related through</span>
            <select
              value={draft.expand.relationTypes[0] ?? ""}
              onChange={(event) => setExpansion("relationTypes", event.target.value)}
            >
              <option value="">No relation expansion</option>
              {(filterOptions.data?.expansionRelationshipTypes ?? []).map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="field compact-field">
            <span>Expansion depth</span>
            <select
              value={String(draft.expand.depth)}
              onChange={(event) => setExpansion("depth", event.target.value)}
              disabled={draft.expand.relationTypes.length === 0}
            >
              <option value="0">No expansion</option>
              <option value="1">One hop</option>
              <option value="2">Two hops</option>
            </select>
          </label>
        </div>
        <ErrorState error={domains.error} />
        <ErrorState error={sources.error} />
      </CollapsibleCard>

      <article className="card ask-chat-panel">
        <h2>Ask Margin</h2>
        <p className="subtle">Answers are grounded only in evidence you are allowed to read.</p>
        {threadId ? (
          <div className="callout" data-testid="ask-thread-context">
            <h3>Conversation</h3>
            <p className="subtle">
              {thread.data?.title ?? "Conversation"} · {thread.data?.messages.length ?? 0} earlier
              turn{(thread.data?.messages.length ?? 0) === 1 ? "" : "s"}
            </p>
            <ul className="stack-list">
              {(thread.data?.messages ?? []).map((message) => (
                <li key={message.id} data-testid="ask-thread-message">
                  <strong>{message.role === "user" ? "You" : "Margin"}</strong>
                  <span className="subtle">{message.content}</span>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <TextAreaField
          label="Question"
          value={draft.query}
          onChange={(query) => setDraft((current) => ({ ...current, query }))}
        />
        <label className="field">
          <span>Answer language</span>
          <select
            value={outputLanguage}
            onChange={(event) => setOutputLanguage(event.target.value)}
            aria-label="Answer language"
          >
            <option value="">Same as my question</option>
            <option value="English">English</option>
            <option value="Arabic">Arabic</option>
            <option value="Chinese">Chinese</option>
            <option value="Hindi">Hindi</option>
            <option value="Japanese">Japanese</option>
            <option value="Urdu">Urdu</option>
          </select>
        </label>
        <div className="actions-row">
          <button
            type="button"
            className="ask-primary-action"
            disabled={answer.loading || !draft.query.trim()}
            onClick={() => {
              setSelectedCitationId(null);
              setRequest({
                id: Date.now(),
                payload: {
                  ...draft,
                  outputLanguage: outputLanguage || undefined,
                  threadId: threadId || null,
                },
              });
            }}
          >
            Ask Margin
          </button>
          <button
            type="button"
            disabled={answer.loading}
            onClick={() => setDraft(defaultRequest())}
          >
            Reset scope
          </button>
        </div>
        {answer.loading ? (
          <LoadingState label="Searching authorized Idea Blocks and preparing cited results..." />
        ) : null}
        <ErrorState error={answer.error} />
        {answer.data ? (
          <>
            <AssistantStateNotice
              responseMode={answer.data.responseMode}
              answerUnavailableReason={answer.data.answerUnavailableReason}
              warnings={answer.data.operationalWarnings}
            />
            {activeFilters.length > 0 ? (
              <p className="subtle">Applied filters: {activeFilters.join(" · ")}</p>
            ) : (
              <p className="subtle">Applied filters: none</p>
            )}
            {answer.data.ambiguity.entityTerms.length > 0 ? (
              <section className="state-message" aria-live="polite">
                <h3>Entity filter needs clarification</h3>
                {answer.data.ambiguity.entityTerms.map((ambiguity) => (
                  <p key={ambiguity.term}>
                    {ambiguity.term}:{" "}
                    {ambiguity.candidateTypes.join(", ") || "multiple catalog matches"}
                  </p>
                ))}
              </section>
            ) : null}
            {answer.data.answer ? (
              <section className="answer-markdown" aria-label="Grounded answer">
                <h3>Answer</h3>
                <div>
                  {inlineAnswerParts.map((part, index) => {
                    if (part.kind === "text")
                      return <MarkdownContent key={`text-${index}`} content={part.value} />;
                    const citation = citationById.get(part.citationId);
                    return citation ? (
                      <CitationInline
                        key={`${citation.id}-${index}`}
                        citationId={citation.id}
                        label={citationLabel(citation)}
                        active={citation.id === selectedCitationId}
                        disabled={citation.redacted}
                        onClick={() => setSelectedCitationId(citation.id)}
                      />
                    ) : (
                      <React.Fragment key={`unknown-${part.citationId}-${index}`}>
                        [unresolved citation]
                      </React.Fragment>
                    );
                  })}
                </div>
              </section>
            ) : null}
            {answer.data.model ? (
              <p className="subtle">
                Model: {answer.data.model.name} ({answer.data.model.version})
                {answer.data.answerLatencyMs === null ? "" : ` · ${answer.data.answerLatencyMs} ms`}
              </p>
            ) : null}
            <h3>Cited results</h3>
            {answer.data.fusion.count > 0 ? (
              <p className="subtle" data-testid="ask-fusion-breakdown">
                Fusion: {answer.data.fusion.mode} ·{" "}
                {Object.entries(answer.data.fusion.channels)
                  .map(([channel, count]) => `${channel} ${count}`)
                  .join(" · ")}
              </p>
            ) : null}
            {answer.data.results.length > 0 ? (
              answer.data.results.map(renderResult)
            ) : (
              <EmptyState label="No authorized Idea Blocks matched this question and scope." />
            )}
            {answer.data.expansion.status === "deadline_exceeded" ? (
              <p className="state-message" role="status">
                Related knowledge could not be loaded before the response deadline.
              </p>
            ) : null}
            {answer.data.expansion.results.length > 0 ? (
              <section aria-label="Related authorized results">
                <h3>Related authorized results</h3>
                {answer.data.expansion.results.map(renderResult)}
              </section>
            ) : null}
          </>
        ) : !answer.loading ? (
          <EmptyState label="Ask a question to view grounded, cited results." />
        ) : null}
      </article>

      <article className="card ask-side-panel">
        <h2>Exact evidence</h2>
        {evidence.loading ? <LoadingState label="Loading cited evidence..." /> : null}
        <ErrorState error={evidence.error} />
        {evidenceCard ? (
          <EvidenceCard
            evidence={{
              ...evidenceCard,
              extractionMethod:
                (evidence.data?.metadata?.extraction_method as string | undefined) ?? "retrieval",
            }}
            onOpenSource={
              selectedCitation?.sourceDocumentId && selectedCitation.sourceElementId
                ? () =>
                    onNavigate("/sources/:sourceId/elements/:elementId", {
                      sourceId: selectedCitation.sourceDocumentId,
                      elementId: selectedCitation.sourceElementId,
                    })
                : undefined
            }
            onOpenIdeaBlock={
              selectedCitation?.ideaBlockId
                ? () =>
                    onNavigate("/knowledge/ideablocks/:ideaBlockId", {
                      ideaBlockId: selectedCitation.ideaBlockId,
                    })
                : undefined
            }
          />
        ) : (
          <EmptyState label="Select a citation to inspect its permitted evidence." />
        )}
      </article>
    </section>
  );
}
