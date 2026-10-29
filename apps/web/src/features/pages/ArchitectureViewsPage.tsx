import React, { Suspense, lazy, useEffect, useMemo, useState } from "react";
import { DiagramPanel } from "../../components/diagrams";
import { EmptyState, ErrorState, LoadingState } from "../../components/layout/States";
import { dashboardClient } from "../../lib/apiDashboard";
import { knowledgeClient } from "../../lib/apiKnowledge";
import type { DiagramSummary, DomainSummary, EntitySummary } from "../../lib/apiTypes";
import { fallbackName, relationLabel, shortId, technicalIdLabel } from "../../lib/display";
import { useLoad } from "../../lib/hooks";
import { AppRoute } from "./types";

const HeatmapChart = lazy(async () => {
  const module = await import("../../components/dashboard/HeatmapChart");
  return { default: module.HeatmapChart };
});

export type ArchitectureViewsPageProps = {
  route: AppRoute;
  params: Record<string, string>;
  onNavigate: (_route: AppRoute, _params?: Record<string, string>) => void;
};

type HeatmapMetric =
  | "healthScore"
  | "staleClaims"
  | "highSeverityRisks"
  | "atdi"
  | "debt"
  | "expiredWaivers"
  | "unownedEntities"
  | "missingAdrCoverage"
  | "missingDrSecurityEvidence";

const asDiagram = (value: unknown): DiagramSummary => {
  const record = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  return {
    id: typeof record.id === "string" ? record.id : "",
    type:
      record.type === "plantuml" || record.type === "structurizr" || record.type === "mermaid"
        ? record.type
        : "mermaid",
    title: fallbackName(
      typeof record.title === "string" ? record.title : "",
      typeof record.id === "string" ? `Diagram ${shortId(record.id)}` : "Diagram"
    ),
    markup: typeof record.markup === "string" ? record.markup : "",
    svg: typeof record.svg === "string" ? record.svg : "",
    evidence: Array.isArray(record.evidence)
      ? (record.evidence as Array<Record<string, unknown>>)
      : [],
  };
};

const isGovernanceEntity = (entity: EntitySummary): boolean => {
  const descriptor = `${entity.kind} ${entity.category}`.toLowerCase();
  return (
    descriptor.includes("adr") ||
    descriptor.includes("risk") ||
    descriptor.includes("waiver") ||
    descriptor.includes("debt")
  );
};

export function ArchitectureViewsPage({
  route,
  params,
  onNavigate,
}: ArchitectureViewsPageProps): React.JSX.Element {
  const heatmap = useLoad("view-heatmap", dashboardClient.heatmap, {
    enabled: route === "/views/heatmap",
  });
  const entities = useLoad("view-entities", knowledgeClient.listEntities, {
    enabled: route === "/views/c4" || route === "/views/c4/:entityId",
  });
  const domains = useLoad("view-domains", () => knowledgeClient.listDomains("read"), {
    enabled: route === "/views/c4" || route === "/views/c4/:entityId",
  });
  const overlays = useLoad("view-overlays", knowledgeClient.c4Overlays, {
    enabled: route === "/views/c4" || route === "/views/c4/:entityId",
  });
  const diagrams = useLoad("view-diagrams", knowledgeClient.diagrams, {
    enabled: route === "/views/c4" || route === "/views/c4/:entityId",
  });
  const [selectedDomainId, setSelectedDomainId] = useState("");
  const [selectedC4EntityId, setSelectedC4EntityId] = useState(params.entityId || "");

  useEffect(() => {
    setSelectedC4EntityId(params.entityId || "");
  }, [params.entityId]);
  const [selectedDiagramId, setSelectedDiagramId] = useState("");
  const [diagramExpanded, setDiagramExpanded] = useState(false);
  const [heatmapMetric, setHeatmapMetric] = useState<HeatmapMetric>("healthScore");
  const [compareDomainIds, setCompareDomainIds] = useState<string[]>([]);

  const domainEntities = useMemo(
    () => (entities.data ?? []).filter((entity) => entity.kind.toLowerCase() === "domain"),
    [entities.data]
  );
  const domainOptions = useMemo<DomainSummary[]>(() => {
    if (domains.data?.length) return domains.data;
    return domainEntities.map((entity) => ({
      id: entity.id,
      name: entity.name,
      owner: entity.owner,
      steward: entity.steward,
      status: entity.status,
      description: entity.description,
    }));
  }, [domainEntities, domains.data]);
  const defaultDomainId = selectedDomainId;
  const systems = (entities.data ?? []).filter(
    (entity) =>
      entity.kind.toLowerCase() === "system" &&
      (!defaultDomainId || entity.domainId === defaultDomainId)
  );
  const containers = (entities.data ?? []).filter(
    (entity) =>
      entity.kind.toLowerCase() === "container" &&
      (!defaultDomainId || entity.domainId === defaultDomainId)
  );
  const components = (entities.data ?? []).filter(
    (entity) =>
      entity.kind.toLowerCase() === "component" &&
      (!defaultDomainId || entity.domainId === defaultDomainId)
  );
  const codePointers = (entities.data ?? []).filter(
    (entity) =>
      entity.kind.toLowerCase() === "code" &&
      (!defaultDomainId || entity.domainId === defaultDomainId)
  );

  const c4EntityId = selectedC4EntityId || params.entityId || "";
  const entityEvidence = useLoad(
    `view-c4-evidence-${c4EntityId || "none"}`,
    () => knowledgeClient.getEntityEvidence(c4EntityId),
    { enabled: (route === "/views/c4" || route === "/views/c4/:entityId") && Boolean(c4EntityId) }
  );
  const entityRelationships = useLoad(
    `view-c4-rel-${c4EntityId || "none"}`,
    () => knowledgeClient.relationshipNeighbors(c4EntityId, { limit: 100 }),
    { enabled: (route === "/views/c4" || route === "/views/c4/:entityId") && Boolean(c4EntityId) }
  );
  const entityById = useMemo(
    () => new Map((entities.data ?? []).map((entity) => [entity.id, entity])),
    [entities.data]
  );
  const directlyRelatedGovernance = useMemo(() => {
    const relatedIds = new Set<string>();
    for (const relationship of entityRelationships.data?.edges ?? []) {
      if (relationship.source.id === c4EntityId) relatedIds.add(relationship.target.id);
      if (relationship.target.id === c4EntityId) relatedIds.add(relationship.source.id);
    }
    return Array.from(relatedIds)
      .map((id) => entityById.get(id))
      .filter((entity): entity is EntitySummary => Boolean(entity))
      .filter((entity) => isGovernanceEntity(entity));
  }, [c4EntityId, entityById, entityRelationships.data?.edges]);

  const effectiveDiagramId = selectedDiagramId;
  const diagramDetail = useLoad(
    `view-diagram-detail-${effectiveDiagramId || "none"}`,
    async () => asDiagram(await knowledgeClient.diagram(effectiveDiagramId)),
    {
      enabled:
        (route === "/views/c4" || route === "/views/c4/:entityId") && Boolean(effectiveDiagramId),
    }
  );

  if (route === "/views/heatmap") {
    const handleHeatmapSelection = (domainId: string): void => {
      setSelectedDomainId(domainId);
      onNavigate("/knowledge/domains/:domainId", { domainId });
    };
    const heatmapData = heatmap.data ?? [];
    const selectedDomain = selectedDomainId
      ? heatmapData.find((domain) => domain.id === selectedDomainId)
      : undefined;
    return (
      <section className="page-grid">
        <article className="card">
          <h2>Heatmap</h2>
          {heatmap.loading ? <LoadingState label="Loading domain heatmap..." /> : null}
          <ErrorState error={heatmap.error} />
          {heatmapData.length > 0 ? (
            <>
              <label className="field">
                <span>Metric</span>
                <select
                  value={heatmapMetric}
                  onChange={(event) => setHeatmapMetric(event.target.value as HeatmapMetric)}
                >
                  <option value="healthScore">health score</option>
                  <option value="staleClaims">stale claims</option>
                  <option value="highSeverityRisks">high severity risks</option>
                  <option value="atdi">ATDI</option>
                  <option value="debt">technical debt</option>
                  <option value="expiredWaivers">expired waivers</option>
                  <option value="unownedEntities">unowned entities</option>
                  <option value="missingAdrCoverage">missing ADR coverage</option>
                  <option value="missingDrSecurityEvidence">missing DR/security evidence</option>
                </select>
              </label>
              <Suspense fallback={<LoadingState label="Loading heatmap..." />}>
                <HeatmapChart
                  data={heatmapData}
                  metric={heatmapMetric}
                  onSelectDomain={handleHeatmapSelection}
                />
              </Suspense>
              <h3>Domain detail filter</h3>
              <div className="actions-row">
                {heatmapData.map((domain) => (
                  <button
                    key={domain.id}
                    type="button"
                    onClick={() => setSelectedDomainId(domain.id)}
                  >
                    {domain.name}
                  </button>
                ))}
              </div>
              <label className="field">
                <span>Compare domains</span>
                <input
                  value={compareDomainIds.join(",")}
                  onChange={(event) =>
                    setCompareDomainIds(
                      event.target.value
                        .split(",")
                        .map((value) => value.trim())
                        .filter(Boolean)
                    )
                  }
                  placeholder="Payments, Identity"
                />
              </label>
              {selectedDomain ? (
                <>
                  <p>
                    Selected {selectedDomain.name}: health {selectedDomain.healthScore}, risks{" "}
                    {selectedDomain.risks}, ATDI {selectedDomain.atdi}, debt {selectedDomain.debt}
                  </p>
                  <p>Missing DR/security evidence: {selectedDomain.missingDrSecurityEvidence}</p>
                  <p>
                    Trend:{" "}
                    {selectedDomain.trend.length > 0
                      ? selectedDomain.trend.join(" -> ")
                      : "No trend history"}
                  </p>
                </>
              ) : null}
              {compareDomainIds.length > 0 ? (
                <div>
                  {compareDomainIds.map((domainId) => {
                    const compared = heatmapData.find(
                      (domain) =>
                        domain.id === domainId ||
                        domain.name.toLowerCase() === domainId.toLowerCase()
                    );
                    if (!compared)
                      return <p key={domainId}>Domain {shortId(domainId)}: not found</p>;
                    return (
                      <p key={domainId}>
                        {compared.name}: health {compared.healthScore} | risks {compared.risks} |
                        debt {compared.debt} | stale {compared.staleClaims}
                      </p>
                    );
                  })}
                </div>
              ) : null}
              <button
                type="button"
                onClick={() => {
                  const payload = JSON.stringify(
                    { metric: heatmapMetric, domains: heatmapData },
                    null,
                    2
                  );
                  if (globalThis.navigator?.clipboard) {
                    void globalThis.navigator.clipboard.writeText(payload);
                  }
                }}
              >
                Export report snapshot
              </button>
              <button
                type="button"
                disabled={!selectedDomain}
                onClick={() =>
                  selectedDomain
                    ? onNavigate("/knowledge/domains/:domainId", {
                        domainId: selectedDomain.id,
                      })
                    : undefined
                }
              >
                Open selected domain detail
              </button>
            </>
          ) : (
            !heatmap.loading && <EmptyState label="No heatmap data available." />
          )}
        </article>
      </section>
    );
  }

  if (route === "/views/c4" || route === "/views/c4/:entityId") {
    const relatedGovernance =
      directlyRelatedGovernance.length > 0
        ? directlyRelatedGovernance.map((entity) => ({
            id: entity.id,
            type: entity.category || entity.kind,
            entity: entity.name,
          }))
        : c4EntityId
          ? (overlays.data ?? []).filter(
              (item) =>
                item.id === c4EntityId ||
                item.entity === c4EntityId ||
                item.entity.toLowerCase().includes(c4EntityId.toLowerCase())
            )
          : [];
    return (
      <section className="page-grid">
        <article className="card">
          <h2>C4 Explorer</h2>
          {entities.loading ? <LoadingState label="Loading architecture entities..." /> : null}
          <ErrorState error={entities.error} />
          {domains.loading ? <LoadingState label="Loading domains..." /> : null}
          <ErrorState error={domains.error} />
          <label className="field">
            <span>Domain focus</span>
            <select
              value={defaultDomainId}
              onChange={(event) => setSelectedDomainId(event.target.value)}
            >
              <option value="">All domains</option>
              {domainOptions.map((domain) => (
                <option key={domain.id} value={domain.id}>
                  {domain.name}
                </option>
              ))}
            </select>
          </label>
          <h3>Domain to system to container to component</h3>
          <p>Level 1 Domain: {domainOptions.map((item) => item.name).join(", ") || "none"}</p>
          <p>Level 2 Containers: {containers.map((item) => item.name).join(", ") || "none"}</p>
          <p>Level 3 Components: {components.map((item) => item.name).join(", ") || "none"}</p>
          <p>Level 4 Code pointers: {codePointers.map((item) => item.name).join(", ") || "none"}</p>
          <p>Systems: {systems.map((item) => item.name).join(", ") || "none"}</p>
          <label className="field">
            <span>Focused entity</span>
            <select
              value={c4EntityId}
              onChange={(event) => setSelectedC4EntityId(event.target.value)}
            >
              <option value="">Choose an entity</option>
              {(entities.data ?? []).map((entity) => (
                <option key={entity.id} value={entity.id}>
                  {entity.name}
                </option>
              ))}
            </select>
          </label>
          <h3>Related governance and evidence</h3>
          {relatedGovernance.length > 0 ? (
            relatedGovernance.map((item) => (
              <p key={item.id}>
                {item.type} - {item.entity}
              </p>
            ))
          ) : (
            <EmptyState label="No governance overlays for selected focus." />
          )}
          {entityEvidence.loading ? <LoadingState label="Loading entity evidence..." /> : null}
          <ErrorState error={entityEvidence.error} />
          {entityEvidence.data && entityEvidence.data.length > 0
            ? entityEvidence.data.map((item) => (
                <p key={item.id}>
                  Evidence: {item.statement} ({item.status})
                </p>
              ))
            : !entityEvidence.loading && <EmptyState label="No evidence for selected entity." />}
          {entityRelationships.loading ? <LoadingState label="Loading relationships..." /> : null}
          <ErrorState error={entityRelationships.error} />
          {entityRelationships.data && entityRelationships.data.edges.length > 0
            ? entityRelationships.data.edges.map((item) => (
                <div key={item.relationshipId}>
                  <p>{relationLabel(item.source.label, item.relationType, item.target.label)}</p>
                  <p className="subtle">
                    {item.status} · {item.provenance.length} authorized evidence item
                    {item.provenance.length === 1 ? "" : "s"}
                  </p>
                  <details className="technical-details">
                    <summary>Technical details</summary>
                    <p className="technical-id">{technicalIdLabel(item.relationshipId)}</p>
                  </details>
                </div>
              ))
            : !entityRelationships.loading && (
                <EmptyState label="No relationships for selected entity." />
              )}
          {entityRelationships.data?.capped ? (
            <p className="state-message" role="status">
              Relationship results are capped at 100. Open the Relationships workspace for a
              narrower bounded view.
            </p>
          ) : null}
          {codePointers.length > 0 ? (
            <>
              <h3>Code references</h3>
              {codePointers.map((entity) => (
                <p key={entity.id}>
                  {entity.name} -{" "}
                  {String(
                    entity.metadata.repo_url ||
                      entity.metadata.repo_path ||
                      entity.metadata.code_pointer ||
                      "reference unavailable"
                  )}
                </p>
              ))}
            </>
          ) : null}
        </article>

        <article className={`card diagram-card ${diagramExpanded ? "expanded-panel" : ""}`}>
          <div className="listview-header">
            <h2>Governed diagrams</h2>
            <button type="button" onClick={() => setDiagramExpanded((value) => !value)}>
              {diagramExpanded ? "Collapse" : "Expand"}
            </button>
          </div>
          {diagrams.loading ? <LoadingState label="Loading diagram registry..." /> : null}
          <ErrorState error={diagrams.error} />
          {diagrams.data && diagrams.data.length > 0 ? (
            <>
              <div className="actions-row">
                {diagrams.data.map((diagram) => (
                  <button
                    key={diagram.id}
                    type="button"
                    onClick={() => setSelectedDiagramId(diagram.id)}
                  >
                    {diagram.type}: {diagram.title}
                  </button>
                ))}
              </div>
              {diagramDetail.data?.evidence && diagramDetail.data.evidence.length > 0 ? (
                <p>Evidence: {String(diagramDetail.data.evidence[0]?.source_path ?? "linked")}</p>
              ) : null}
              <ErrorState error={diagramDetail.error} />
              <DiagramPanel diagram={diagramDetail.data ?? null} />
            </>
          ) : (
            !diagrams.loading && <EmptyState label="No governed diagrams available." />
          )}
        </article>
      </section>
    );
  }

  return <EmptyState label="Choose an architecture view from the navigation." />;
}
