import React, { useEffect, useMemo, useState } from "react";
import { EmptyState, ErrorState, LoadingState } from "../../components/layout/States";
import { DataTable } from "../../components/tables";
import { governanceClient } from "../../lib/apiGovernance";
import { knowledgeClient } from "../../lib/apiKnowledge";
import type { ExplicitRelationshipEdge, IdeaBlockRelationship } from "../../lib/apiTypes";
import { shortId } from "../../lib/display";
import { useLoad } from "../../lib/hooks";
import { AppRoute } from "./types";

type Navigate = (_route: AppRoute, _params?: Record<string, string>) => void;

type RelationshipKnowledgeViewProps = {
  params: Record<string, string>;
  onNavigate: Navigate;
};

const validityLabel = (validFrom: string | null, validTo: string | null): string =>
  `${validFrom || "No start date"} to ${validTo || "Current"}`;

const relationshipError = (error: string | null): string | null => {
  if (!error) return null;
  const value = error.toLowerCase();
  if (value.includes("permission") || value.includes("forbidden") || value.includes("denied")) {
    return "You do not have permission to view these relationships.";
  }
  if (value.includes("deadline") || value.includes("timed out") || value.includes("timeout")) {
    return "The relationship query timed out. Narrow the path or choose a smaller depth.";
  }
  if (value.includes("invalid") || value.includes("max depth") || value.includes("limit")) {
    return "The relationship query is invalid. Choose valid entities and a depth between 1 and 4.";
  }
  if (value.includes("postgres")) {
    return "Dependency paths and impact paths require the PostgreSQL relationship service.";
  }
  return error;
};

function RelationshipEvidence({
  edge,
  onNavigate,
}: {
  edge: ExplicitRelationshipEdge;
  onNavigate: Navigate;
}): React.JSX.Element {
  return (
    <details>
      <summary>{`Evidence (${edge.provenance.length})`}</summary>
      {edge.provenance.length ? (
        <ul className="detail-list">
          {edge.provenance.map((item) => (
            <li key={item.id}>
              <p>{item.sourceQuote || "No exact source quote is available."}</p>
              <p className="subtle">
                {item.status} · {item.polarity} · {validityLabel(item.validFrom, item.validTo)}
              </p>
              {item.ideaBlockId ? (
                <button
                  type="button"
                  className="table-link"
                  onClick={() =>
                    onNavigate("/knowledge/ideablocks/:ideaBlockId", {
                      ideaBlockId: item.ideaBlockId,
                    })
                  }
                >
                  Open IdeaBlock {shortId(item.ideaBlockId)}
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="subtle">No authorized provenance is available for this relationship.</p>
      )}
    </details>
  );
}

function RelationshipEdgeTable({
  edges,
  emptyLabel,
  onNavigate,
}: {
  edges: ExplicitRelationshipEdge[];
  emptyLabel: string;
  onNavigate: Navigate;
}): React.JSX.Element {
  return (
    <DataTable
      rows={edges}
      emptyLabel={emptyLabel}
      getRowKey={(edge) => `${edge.relationshipId}-${edge.hop}`}
      density="compact"
      columns={[
        {
          header: "Relationship",
          render: (edge) => (
            <span className="source-title-cell">
              <button
                type="button"
                className="table-link"
                onClick={() =>
                  onNavigate("/knowledge/relationships/entity/:entityId", {
                    entityId: edge.source.id,
                  })
                }
              >
                {edge.source.label}
              </button>
              <span>{edge.relationType}</span>
              <button
                type="button"
                className="table-link"
                onClick={() =>
                  onNavigate("/knowledge/relationships/entity/:entityId", {
                    entityId: edge.target.id,
                  })
                }
              >
                {edge.target.label}
              </button>
            </span>
          ),
          sortValue: (edge) => `${edge.source.label} ${edge.relationType} ${edge.target.label}`,
        },
        {
          header: "Direction",
          render: (edge) => edge.direction,
          sortValue: (edge) => edge.direction,
        },
        {
          header: "Review state",
          render: (edge) => (
            <span className="badge">
              {edge.status} · {edge.confidence}
            </span>
          ),
          sortValue: (edge) => edge.status,
        },
        {
          header: "Validity",
          render: (edge) => validityLabel(edge.validFrom, edge.validTo),
          sortValue: (edge) => edge.validFrom || "",
        },
        {
          header: "Exact evidence",
          render: (edge) => <RelationshipEvidence edge={edge} onNavigate={onNavigate} />,
        },
      ]}
    />
  );
}

function ImpactPathEvidence({
  edges,
  onNavigate,
}: {
  edges: ExplicitRelationshipEdge[];
  onNavigate: Navigate;
}): React.JSX.Element {
  return (
    <details>
      <summary>{`Path evidence (${edges.length} relationships)`}</summary>
      {edges.length ? (
        <ul className="detail-list">
          {edges.map((edge) => (
            <li key={`${edge.relationshipId}-${edge.hop}`}>
              <p>
                {edge.source.label} · {edge.relationType} · {edge.target.label}
              </p>
              <RelationshipEvidence edge={edge} onNavigate={onNavigate} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="subtle">No path evidence was returned.</p>
      )}
    </details>
  );
}

function EntityRelationshipExplorer({
  initialEntityId,
  onNavigate,
}: {
  initialEntityId: string;
  onNavigate: Navigate;
}): React.JSX.Element {
  const entities = useLoad("relationship-explorer-entities", knowledgeClient.listEntities);
  const [entityId, setEntityId] = useState(initialEntityId);
  const [targetEntityId, setTargetEntityId] = useState("");
  const [maxDepth, setMaxDepth] = useState(2);
  const [direction, setDirection] = useState<"inbound" | "outbound" | "both">("both");
  const [includeCandidates, setIncludeCandidates] = useState(false);

  useEffect(() => {
    setEntityId(initialEntityId);
    setTargetEntityId("");
  }, [initialEntityId]);

  const entityOptions = useMemo(
    () => [...(entities.data ?? [])].sort((left, right) => left.name.localeCompare(right.name)),
    [entities.data]
  );
  const neighbors = useLoad(
    `relationship-neighbors-${entityId}-${includeCandidates}`,
    () => knowledgeClient.relationshipNeighbors(entityId, { includeCandidates, limit: 100 }),
    { enabled: Boolean(entityId) }
  );
  const path = useLoad(
    `relationship-path-${entityId}-${targetEntityId}-${maxDepth}-${includeCandidates}`,
    () =>
      knowledgeClient.relationshipPath({
        sourceEntityId: entityId,
        targetEntityId,
        maxDepth,
        includeCandidates,
      }),
    { enabled: Boolean(entityId && targetEntityId && entityId !== targetEntityId) }
  );
  const impact = useLoad(
    `relationship-impact-${entityId}-${maxDepth}-${direction}-${includeCandidates}`,
    () => knowledgeClient.relationshipImpact(entityId, { maxDepth, direction, includeCandidates }),
    { enabled: Boolean(entityId) }
  );
  const governed = useLoad(
    `relationship-governance-${entityId}`,
    () => knowledgeClient.governedEntityRelationships(entityId, { limit: 100 }),
    { enabled: Boolean(entityId) }
  );

  if (entities.loading) return <LoadingState label="Loading entities for relationships..." />;
  if (entities.error) return <ErrorState error={relationshipError(entities.error)} />;

  return (
    <section className="page-grid">
      <article className="card">
        <p className="eyebrow">Relationships</p>
        <h2>Entity relationships</h2>
        <p className="subtle">
          Inspect direct, evidence-backed relationships first. Dependency and impact paths are
          bounded.
        </p>
        <div className="filter-grid">
          <label className="field">
            <span>Entity</span>
            <select
              value={entityId}
              onChange={(event) => {
                setEntityId(event.target.value);
                setTargetEntityId("");
              }}
            >
              <option value="">Choose an entity</option>
              {entityOptions.map((entity) => (
                <option key={entity.id} value={entity.id}>
                  {entity.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Include candidate relationships</span>
            <input
              type="checkbox"
              checked={includeCandidates}
              onChange={(event) => setIncludeCandidates(event.target.checked)}
            />
          </label>
        </div>
      </article>

      <article className="card">
        <h3>Direct relationships</h3>
        {!entityId ? (
          <EmptyState label="Choose an entity to view its direct relationships." />
        ) : null}
        {neighbors.loading ? <LoadingState label="Loading direct relationships..." /> : null}
        <ErrorState error={relationshipError(neighbors.error)} />
        {neighbors.data ? (
          <>
            <RelationshipEdgeTable
              edges={neighbors.data.edges}
              emptyLabel="No direct relationships match this view."
              onNavigate={onNavigate}
            />
            {neighbors.data.capped ? (
              <p className="state-message" role="status">
                Results are capped. Refine the selection or view a bounded path.
              </p>
            ) : null}
          </>
        ) : null}
      </article>

      <article className="card">
        <h3>Bounded dependency path</h3>
        <div className="filter-grid">
          <label className="field">
            <span>Target entity</span>
            <select
              value={targetEntityId}
              onChange={(event) => setTargetEntityId(event.target.value)}
              disabled={!entityId}
            >
              <option value="">Choose a target</option>
              {entityOptions
                .filter((entity) => entity.id !== entityId)
                .map((entity) => (
                  <option key={entity.id} value={entity.id}>
                    {entity.name}
                  </option>
                ))}
            </select>
          </label>
          <label className="field">
            <span>Maximum depth</span>
            <select value={maxDepth} onChange={(event) => setMaxDepth(Number(event.target.value))}>
              {[1, 2, 3, 4].map((depth) => (
                <option key={depth} value={depth}>
                  {depth} hop{depth === 1 ? "" : "s"}
                </option>
              ))}
            </select>
          </label>
        </div>
        {!targetEntityId ? (
          <EmptyState label="Choose a target to view a bounded dependency path." />
        ) : null}
        {path.loading ? <LoadingState label="Loading bounded dependency path..." /> : null}
        <ErrorState error={relationshipError(path.error)} />
        {path.data ? (
          path.data.found ? (
            <RelationshipEdgeTable
              edges={path.data.path}
              emptyLabel="No path returned."
              onNavigate={onNavigate}
            />
          ) : (
            <EmptyState label={`No path was found within ${maxDepth} hops.`} />
          )
        ) : null}
        {path.data?.capped ? (
          <p className="state-message" role="status">
            This path result is capped.
          </p>
        ) : null}
      </article>

      <article className="card">
        <h3>Bounded impact paths</h3>
        <div className="filter-grid">
          <label className="field">
            <span>Direction</span>
            <select
              value={direction}
              onChange={(event) =>
                setDirection(event.target.value as "inbound" | "outbound" | "both")
              }
            >
              <option value="both">Inbound and outbound</option>
              <option value="outbound">Outbound</option>
              <option value="inbound">Inbound</option>
            </select>
          </label>
        </div>
        {!entityId ? <EmptyState label="Choose an entity to view bounded impact paths." /> : null}
        {impact.loading ? <LoadingState label="Loading bounded impact paths..." /> : null}
        <ErrorState error={relationshipError(impact.error)} />
        {impact.data ? (
          <DataTable
            rows={impact.data.impacts}
            emptyLabel="No impacted entities match this direction and depth."
            getRowKey={(item) => `${item.entity.id}-${item.hop}`}
            density="compact"
            columns={[
              {
                header: "Impacted entity",
                render: (item) => (
                  <button
                    type="button"
                    className="table-link"
                    onClick={() =>
                      onNavigate("/knowledge/relationships/entity/:entityId", {
                        entityId: item.entity.id,
                      })
                    }
                  >
                    {item.entity.label}
                  </button>
                ),
                sortValue: (item) => item.entity.label,
              },
              { header: "Hops", render: (item) => String(item.hop), sortValue: (item) => item.hop },
              {
                header: "Relationship",
                render: (item) => item.relationType || "Not classified",
                sortValue: (item) => item.relationType || "",
              },
              {
                header: "Path evidence",
                render: (item) => <ImpactPathEvidence edges={item.path} onNavigate={onNavigate} />,
              },
            ]}
          />
        ) : null}
        {impact.data?.capped ? (
          <p className="state-message" role="status">
            Impact results are capped. Narrow the direction or depth.
          </p>
        ) : null}
      </article>

      <article className="card">
        <h3>Governance links</h3>
        {!entityId ? <EmptyState label="Choose an entity to view its governance links." /> : null}
        {governed.loading ? <LoadingState label="Loading governance links..." /> : null}
        <ErrorState error={relationshipError(governed.error)} />
        {governed.data ? (
          <>
            <DataTable
              rows={governed.data.relationships}
              emptyLabel="No governance records are linked to this entity."
              getRowKey={(record) => record.governanceRecordId}
              density="compact"
              columns={[
                {
                  header: "Governance record",
                  render: (record) => (
                    <button
                      type="button"
                      className="table-link"
                      onClick={() =>
                        onNavigate("/governance/records/:recordId", {
                          recordId: record.governanceRecordId,
                        })
                      }
                    >
                      {record.title}
                    </button>
                  ),
                  sortValue: (record) => record.title,
                },
                {
                  header: "Business key",
                  render: (record) => record.businessKey,
                  sortValue: (record) => record.businessKey,
                },
                {
                  header: "Type",
                  render: (record) => record.recordType,
                  sortValue: (record) => record.recordType,
                },
                {
                  header: "Review state",
                  render: (record) => record.lifecycleStatus,
                  sortValue: (record) => record.lifecycleStatus,
                },
                {
                  header: "Role",
                  render: (record) => record.role,
                  sortValue: (record) => record.role,
                },
              ]}
            />
            {governed.data.capped ? (
              <p className="state-message" role="status">
                Governance links are capped at 100 results. Choose a different entity to inspect a
                narrower relationship scope.
              </p>
            ) : null}
          </>
        ) : null}
      </article>
    </section>
  );
}

function IdeaBlockRelationships({
  ideaBlockId,
  onNavigate,
}: {
  ideaBlockId: string;
  onNavigate: Navigate;
}): React.JSX.Element {
  const relationships = useLoad(
    `idea-block-relationships-${ideaBlockId}`,
    () => knowledgeClient.relatedIdeaBlockRelationships(ideaBlockId, { limit: 100 }),
    { enabled: Boolean(ideaBlockId) }
  );
  return (
    <section className="page-grid">
      <article className="card">
        <p className="eyebrow">Relationships</p>
        <h2>IdeaBlock semantic relationships</h2>
        <p className="subtle">
          Only explicit semantic relationships and their decision provenance are shown.
        </p>
        <button
          type="button"
          onClick={() => onNavigate("/knowledge/ideablocks/:ideaBlockId", { ideaBlockId })}
        >
          Back to IdeaBlock
        </button>
      </article>
      <article className="card">
        {relationships.loading ? <LoadingState label="Loading IdeaBlock relationships..." /> : null}
        <ErrorState error={relationshipError(relationships.error)} />
        {relationships.data ? (
          <>
            <DataTable
              rows={relationships.data.relations}
              emptyLabel="No explicit semantic relationships are linked to this IdeaBlock."
              getRowKey={(relation) => relation.relationshipId}
              density="compact"
              columns={[
                {
                  header: "Relationship",
                  render: (relation) => (
                    <IdeaBlockRelationLabel relation={relation} onNavigate={onNavigate} />
                  ),
                  sortValue: (relation) => relation.relationType,
                },
                {
                  header: "Direction",
                  render: (relation) => relation.direction,
                  sortValue: (relation) => relation.direction,
                },
                {
                  header: "Review state",
                  render: (relation) => `${relation.status} · ${relation.confidence}`,
                  sortValue: (relation) => relation.status,
                },
                {
                  header: "Validity",
                  render: (relation) => validityLabel(relation.validFrom, relation.validTo),
                  sortValue: (relation) => relation.validFrom || "",
                },
                {
                  header: "Decision provenance",
                  render: (relation) => (
                    <details>
                      <summary>View decision provenance</summary>
                      <pre className="code-block">
                        {JSON.stringify(relation.decisionProvenance, null, 2)}
                      </pre>
                    </details>
                  ),
                },
              ]}
            />
            {relationships.data.capped ? (
              <p className="state-message" role="status">
                Results are capped. Narrow the relationship request.
              </p>
            ) : null}
          </>
        ) : null}
      </article>
    </section>
  );
}

function IdeaBlockRelationLabel({
  relation,
  onNavigate,
}: {
  relation: IdeaBlockRelationship;
  onNavigate: Navigate;
}): React.JSX.Element {
  return (
    <span className="source-title-cell">
      <button
        type="button"
        className="table-link"
        onClick={() =>
          onNavigate("/knowledge/ideablocks/:ideaBlockId", { ideaBlockId: relation.source.id })
        }
      >
        {relation.source.statement || shortId(relation.source.id)}
      </button>
      <span>{relation.relationType}</span>
      <button
        type="button"
        className="table-link"
        onClick={() =>
          onNavigate("/knowledge/ideablocks/:ideaBlockId", { ideaBlockId: relation.target.id })
        }
      >
        {relation.target.statement || shortId(relation.target.id)}
      </button>
    </span>
  );
}

function GovernanceRelationshipContext({
  recordId,
  onNavigate,
}: {
  recordId: string;
  onNavigate: Navigate;
}): React.JSX.Element {
  const record = useLoad(`governance-relationship-context-${recordId}`, () =>
    governanceClient.record(recordId)
  );
  return (
    <section className="page-grid">
      <article className="card">
        <p className="eyebrow">Relationships</p>
        <h2>Governance relationship context</h2>
        {record.loading ? (
          <LoadingState label="Loading governance relationship context..." />
        ) : null}
        <ErrorState error={relationshipError(record.error)} />
        {record.data ? (
          <p className="subtle">
            Choose a governed entity from {record.data.title} to inspect its direct relationships,
            paths, and exact evidence.
          </p>
        ) : null}
      </article>
      {record.data ? (
        <article className="card">
          <DataTable
            rows={record.data.entities}
            emptyLabel="No entities are linked to this governance record."
            getRowKey={(entity) => `${entity.id}-${entity.role}`}
            density="compact"
            columns={[
              {
                header: "Entity",
                render: (entity) => (
                  <button
                    type="button"
                    className="table-link"
                    onClick={() =>
                      onNavigate("/knowledge/relationships/entity/:entityId", {
                        entityId: entity.id,
                      })
                    }
                  >
                    {shortId(entity.id)}
                  </button>
                ),
                sortValue: (entity) => entity.id,
              },
              {
                header: "Role",
                render: (entity) => entity.role,
                sortValue: (entity) => entity.role,
              },
            ]}
          />
          <button
            type="button"
            onClick={() => onNavigate("/governance/records/:recordId", { recordId })}
          >
            Back to governance record
          </button>
        </article>
      ) : null}
    </section>
  );
}

export function RelationshipKnowledgeView({
  params,
  onNavigate,
}: RelationshipKnowledgeViewProps): React.JSX.Element {
  if (params.ideaBlockId)
    return <IdeaBlockRelationships ideaBlockId={params.ideaBlockId} onNavigate={onNavigate} />;
  if (params.recordId)
    return <GovernanceRelationshipContext recordId={params.recordId} onNavigate={onNavigate} />;
  return (
    <EntityRelationshipExplorer initialEntityId={params.entityId || ""} onNavigate={onNavigate} />
  );
}
