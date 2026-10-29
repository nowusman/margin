import React, { Suspense, lazy, useMemo, useState } from "react";
import { EmptyState, ErrorState, LoadingState } from "../../components/layout/States";
import { graphClient } from "../../lib/apiGraph";
import { governanceClient } from "../../lib/apiGovernance";
import type { GraphEdge, GraphKnowledgeGap, GraphNode } from "../../lib/apiTypes";
import { useCommand, useLoad } from "../../lib/hooks";
import { AppRoute } from "./types";

const GraphMap = lazy(async () => {
  const module = await import("../../components/graph/GraphMap");
  return { default: module.GraphMap };
});

export type GraphPageProps = {
  route: AppRoute;
  params: Record<string, string>;
  onNavigate: (
    _route: AppRoute,
    _params?: Record<string, string>,
    _query?: Record<string, string>
  ) => void;
};

const researchQuestion = (gap: GraphKnowledgeGap): string => {
  const focus = gap.researchTerm.trim() || gap.title.trim();
  if (!focus) return "";
  const context = gap.researchContext.trim();
  return context ? `What is known about ${focus} and ${context}?` : `What is known about ${focus}?`;
};

/**
 * Ask the reader why they are dismissing a finding.
 *
 * A dismissal is a decision about what the reader wants to see, and the reason
 * is what makes it reviewable later. An empty answer records no reason rather
 * than refusing the dismissal the reader asked for.
 */
const dismissalReason = (question: string): string => window.prompt(question)?.trim() ?? "";

export function GraphPage({ route, params, onNavigate }: GraphPageProps): React.JSX.Element {
  const rootEntityId = params.entityId ?? "";
  const [depth, setDepth] = useState(1);
  const [selectedEdge, setSelectedEdge] = useState<GraphEdge | null>(null);
  const [selectedKinds, setSelectedKinds] = useState<string[]>([]);
  const [selectedRelations, setSelectedRelations] = useState<string[]>([]);
  const [selectedStates, setSelectedStates] = useState<string[]>([]);
  const [expandedNodes, setExpandedNodes] = useState<Record<string, GraphNode[]>>({});
  const [expandedEdges, setExpandedEdges] = useState<Record<string, GraphEdge[]>>({});
  const command = useCommand();

  const queryKey = `graph-${rootEntityId || "all"}-${depth}`;
  const graphRoute = route === "/views/graph" || route === "/views/graph/:entityId";
  const subgraph = useLoad(queryKey, () => graphClient.subgraph({ rootEntityId, depth }), {
    enabled: graphRoute,
  });
  const insights = useLoad(queryKey, () => graphClient.insights({ rootEntityId, depth }), {
    enabled: graphRoute,
  });

  const mergedNodes = useMemo(() => {
    const byId = new Map<string, GraphNode>();
    (subgraph.data?.nodes ?? []).forEach((node) => byId.set(node.id, node));
    Object.values(expandedNodes).forEach((group) =>
      group.forEach((node) => {
        if (!byId.has(node.id)) byId.set(node.id, node);
      })
    );
    return [...byId.values()];
  }, [subgraph.data, expandedNodes]);

  const mergedEdges = useMemo(() => {
    const byId = new Map<string, GraphEdge>();
    (subgraph.data?.edges ?? []).forEach((edge) => byId.set(edge.id, edge));
    Object.values(expandedEdges).forEach((group) =>
      group.forEach((edge) => {
        if (!byId.has(edge.id)) byId.set(edge.id, edge);
      })
    );
    return [...byId.values()];
  }, [subgraph.data, expandedEdges]);

  const availableKinds = useMemo(() => {
    const seen: string[] = [];
    mergedNodes.forEach((node) => {
      if (!seen.includes(node.kind)) seen.push(node.kind);
    });
    return seen.sort();
  }, [mergedNodes]);

  const availableRelations = useMemo(() => {
    const seen: string[] = [];
    mergedEdges.forEach((edge) => {
      if (!seen.includes(edge.relationType)) seen.push(edge.relationType);
    });
    return seen.sort();
  }, [mergedEdges]);

  const availableStates = useMemo(() => {
    const seen: string[] = [];
    mergedEdges.forEach((edge) => {
      if (!seen.includes(edge.status)) seen.push(edge.status);
    });
    return seen.sort();
  }, [mergedEdges]);

  const relationFilteredEdges = useMemo(
    () =>
      selectedRelations.length === 0
        ? mergedEdges
        : mergedEdges.filter((edge) => selectedRelations.includes(edge.relationType)),
    [mergedEdges, selectedRelations]
  );

  const visibleNodes = useMemo(() => {
    const byKind =
      selectedKinds.length === 0
        ? mergedNodes
        : mergedNodes.filter((node) => selectedKinds.includes(node.kind));
    if (selectedRelations.length === 0 && selectedStates.length === 0) return byKind;
    const related = new Set(
      relationFilteredEdges
        .filter((edge) => selectedStates.length === 0 || selectedStates.includes(edge.status))
        .flatMap((edge) => [edge.sourceId, edge.targetId])
    );
    return byKind.filter((node) => related.has(node.id));
  }, [mergedNodes, selectedKinds, selectedRelations, selectedStates, relationFilteredEdges]);

  const visibleNodeIds = useMemo(
    () => new Set(visibleNodes.map((node) => node.id)),
    [visibleNodes]
  );

  const visibleEdges = useMemo(
    () =>
      relationFilteredEdges.filter(
        (edge) =>
          (selectedStates.length === 0 || selectedStates.includes(edge.status)) &&
          visibleNodeIds.has(edge.sourceId) &&
          visibleNodeIds.has(edge.targetId)
      ),
    [relationFilteredEdges, selectedStates, visibleNodeIds]
  );

  const nodeById = useMemo(() => {
    const lookup = new Map<string, string>();
    mergedNodes.forEach((node) => lookup.set(node.id, node.label));
    return lookup;
  }, [mergedNodes]);

  const labelFor = (nodeId: string): string => nodeById.get(nodeId) ?? nodeId;

  const expandNode = (nodeId: string): void => {
    if (expandedNodes[nodeId]) return;
    command.runCommand("Expand the record", async () => {
      const result = await graphClient.subgraph({ rootEntityId: nodeId, depth: 1 });
      setExpandedNodes((previous) => ({ ...previous, [nodeId]: result.nodes }));
      setExpandedEdges((previous) => ({ ...previous, [nodeId]: result.edges }));
    });
  };

  if (subgraph.loading) return <LoadingState label="Loading the knowledge graph" />;
  if (subgraph.error) return <ErrorState error={subgraph.error} />;
  if (!subgraph.data)
    return <EmptyState label="No graph is available for your current access scope." />;

  const { communities, capped, ontologyVersion } = subgraph.data;
  const hiddenCount = insights.data?.hiddenCount ?? 0;

  if (mergedNodes.length === 0)
    return (
      <EmptyState label="No readable records are in scope. Import a source document to begin." />
    );

  return (
    <section className="page-grid">
      <article className="card">
        <header className="card-header">
          <div>
            <h2>Knowledge graph</h2>
            <p className="subtle">
              {visibleNodes.length} record{visibleNodes.length === 1 ? "" : "s"} and{" "}
              {visibleEdges.length} evidenced relationship
              {visibleEdges.length === 1 ? "" : "s"} you can read.
            </p>
            {hiddenCount > 0 ? (
              <p className="subtle" data-testid="graph-hidden-count">
                {hiddenCount} finding{hiddenCount === 1 ? " is" : "s are"} hidden by your decisions.
              </p>
            ) : null}
          </div>
          <div className="field">
            <span>Depth</span>
            <select
              value={depth}
              onChange={(event) => setDepth(Number(event.target.value))}
              aria-label="Graph depth"
            >
              <option value={1}>1 hop</option>
              <option value={2}>2 hops</option>
              <option value={3}>3 hops</option>
            </select>
          </div>
        </header>

        <div className="actions-row">
          <label className="field">
            <span>Kind</span>
            <select
              multiple
              value={selectedKinds}
              onChange={(event) =>
                setSelectedKinds(Array.from(event.target.selectedOptions, (option) => option.value))
              }
              aria-label="Graph kind filter"
              size={Math.min(4, Math.max(2, availableKinds.length))}
            >
              {availableKinds.map((kind) => (
                <option key={kind} value={kind}>
                  {kind}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Relationship</span>
            <select
              multiple
              value={selectedRelations}
              onChange={(event) =>
                setSelectedRelations(
                  Array.from(event.target.selectedOptions, (option) => option.value)
                )
              }
              aria-label="Graph relationship filter"
              size={Math.min(4, Math.max(2, availableRelations.length))}
            >
              {availableRelations.map((relation) => (
                <option key={relation} value={relation}>
                  {relation}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Knowledge state</span>
            <select
              multiple
              value={selectedStates}
              onChange={(event) =>
                setSelectedStates(
                  Array.from(event.target.selectedOptions, (option) => option.value)
                )
              }
              aria-label="Graph knowledge state filter"
              size={Math.min(4, Math.max(2, availableStates.length))}
            >
              {availableStates.map((state) => (
                <option key={state} value={state}>
                  {state}
                </option>
              ))}
            </select>
          </label>
          {selectedKinds.length > 0 || selectedRelations.length > 0 || selectedStates.length > 0 ? (
            <button
              type="button"
              onClick={() => {
                setSelectedKinds([]);
                setSelectedRelations([]);
                setSelectedStates([]);
              }}
            >
              Clear filters
            </button>
          ) : null}
        </div>

        {capped ? (
          <p className="notice" role="status">
            This view was capped to keep it readable. Narrow the scope to see the rest.
          </p>
        ) : null}

        <Suspense fallback={<LoadingState label="Preparing the graph" />}>
          <GraphMap
            nodes={visibleNodes}
            edges={visibleEdges}
            onSelectEdge={setSelectedEdge}
            onSelectNode={expandNode}
          />
        </Suspense>

        {selectedEdge ? (
          <div className="callout" data-testid="graph-edge-detail">
            <h3>
              {labelFor(selectedEdge.sourceId)} → {labelFor(selectedEdge.targetId)}
            </h3>
            <p className="subtle">
              {selectedEdge.relationType} · {selectedEdge.confidence} · {selectedEdge.status}
            </p>
            {selectedEdge.quotes.length > 0 ? (
              <blockquote data-testid="graph-edge-quote">{selectedEdge.quotes[0]}</blockquote>
            ) : (
              <p className="subtle">No readable quote is attached to this relationship.</p>
            )}
          </div>
        ) : null}
      </article>

      <article className="card">
        <h3>Records in this view</h3>
        <p className="subtle">
          Expand a record to pull in its neighbourhood, or open the record itself.
        </p>
        <ul className="stack-list" data-testid="graph-record-list">
          {visibleNodes.map((node) => (
            <li key={node.id} data-testid="graph-record">
              <strong>{node.label}</strong>
              <span className="subtle">
                {node.kind} · {node.degree} connection{node.degree === 1 ? "" : "s"}
                {node.communityId ? ` · ${node.communityId}` : ""}
              </span>
              <div className="actions-row">
                <button
                  type="button"
                  onClick={() => expandNode(node.id)}
                  data-testid="graph-record-expand"
                >
                  {expandedNodes[node.id] ? "Expanded" : "Expand"}
                </button>
                <button
                  type="button"
                  onClick={() => onNavigate("/knowledge/entities/:entityId", { entityId: node.id })}
                  data-testid="graph-record-open"
                >
                  Open record
                </button>
              </div>
            </li>
          ))}
        </ul>
        {command.message ? <p className="subtle">{command.message}</p> : null}
      </article>

      <article className="card">
        <h3>Communities</h3>
        <p className="subtle">
          Records that belong together, resolved from the relationships you can read.
        </p>
        <ul className="stack-list" data-testid="graph-communities">
          {communities.map((community) => (
            <li key={community.communityId} data-testid="graph-community">
              <strong>{community.memberIds.length} records</strong>
              <span className="subtle">
                cohesion {community.cohesion.toFixed(2)} · {community.internalEdgeCount} internal
                connection
                {community.internalEdgeCount === 1 ? "" : "s"} · {community.boundaryEdgeCount}{" "}
                outward
              </span>
              <span className="subtle">
                {community.memberIds.map((memberId) => labelFor(memberId)).join(", ")}
              </span>
            </li>
          ))}
        </ul>
        <p className="subtle">Ontology version {ontologyVersion}.</p>
      </article>

      <article className="card">
        <h3>Surprising connections</h3>
        {insights.loading ? <LoadingState label="Looking for surprises" /> : null}
        {insights.error ? <ErrorState error={insights.error} /> : null}
        {insights.data && insights.data.surprisingConnections.length === 0 ? (
          <p className="subtle">No connections cross a boundary in the current scope.</p>
        ) : null}
        <ul className="stack-list" data-testid="graph-surprises">
          {(insights.data?.surprisingConnections ?? []).map((connection) => (
            <li key={connection.edgeId} data-testid="graph-surprise">
              <strong>
                {labelFor(connection.sourceId)} → {labelFor(connection.targetId)}
              </strong>
              <span className="subtle">score {connection.score}</span>
              <span className="subtle">{connection.reasons.join(" · ")}</span>
              <button
                type="button"
                onClick={() =>
                  command.runCommand("Dismiss connection", () =>
                    graphClient
                      .decide(
                        connection.edgeId,
                        "connection",
                        "dismiss",
                        dismissalReason("Why is this connection not useful?")
                      )
                      .then(() => insights.refresh())
                  )
                }
              >
                Dismiss
              </button>
            </li>
          ))}
        </ul>
      </article>

      <article className="card">
        <h3>Knowledge gaps</h3>
        <p className="subtle">
          Where the graph is thin, what would close it, and what you want done about it.
        </p>
        {insights.loading ? <LoadingState label="Looking for gaps" /> : null}
        {insights.error ? <ErrorState error={insights.error} /> : null}
        {insights.data && insights.data.knowledgeGaps.length === 0 ? (
          <p className="subtle">No gaps were detected in the current scope.</p>
        ) : null}
        <ul className="stack-list" data-testid="graph-gaps">
          {(insights.data?.knowledgeGaps ?? []).map((gap) => (
            <li key={gap.gapKey} data-testid="graph-gap">
              <strong>{gap.title}</strong>
              <span className="subtle">{gap.description}</span>
              <span className="subtle">{gap.suggestion}</span>
              {gap.affectedRecords.length > 0 ? (
                <span className="subtle" data-testid="graph-gap-records">
                  Affected records:{" "}
                  {gap.affectedRecords
                    .map((record) => `${record.label} (${record.kind})`)
                    .join(", ")}
                </span>
              ) : null}
              <div className="actions-row">
                <button
                  type="button"
                  onClick={() => {
                    const question = researchQuestion(gap);
                    onNavigate("/ask", {}, question ? { q: question } : {});
                  }}
                  data-testid="graph-gap-research"
                >
                  Investigate {gap.researchTerm || gap.title}
                </button>
                <button
                  type="button"
                  onClick={() =>
                    command.runCommand("Create a research task", () =>
                      governanceClient
                        .raiseReviewTask({
                          target_type: "knowledge_gap",
                          target_id: gap.gapKey,
                          task_type: "research:knowledge_gap",
                          reason: gap.description || gap.title,
                          recommendation: gap.suggestion,
                        })
                        .then(() => insights.refresh())
                    )
                  }
                  data-testid="graph-gap-create"
                >
                  Create a task
                </button>
                <button
                  type="button"
                  onClick={() =>
                    command.runCommand("Defer the gap", () =>
                      graphClient.decide(gap.gapKey, "gap", "defer").then(() => insights.refresh())
                    )
                  }
                  data-testid="graph-gap-defer"
                >
                  Defer this gap
                </button>
                <button
                  type="button"
                  onClick={() =>
                    command.runCommand("Dismiss the gap", () =>
                      graphClient
                        .decide(
                          gap.gapKey,
                          "gap",
                          "dismiss",
                          dismissalReason("Why does this gap not need closing?")
                        )
                        .then(() => insights.refresh())
                    )
                  }
                  data-testid="graph-gap-dismiss"
                >
                  Dismiss this gap
                </button>
              </div>
            </li>
          ))}
        </ul>
        {command.message ? <p className="subtle">{command.message}</p> : null}
      </article>
    </section>
  );
}
