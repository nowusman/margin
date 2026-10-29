import { ApiError, apiClient } from "./api-client";
import { asArray, bool, num, rec, str } from "./apiMappers";
import type {
  GraphCommunity,
  GraphEdge,
  GraphInsights,
  GraphKnowledgeGap,
  GraphNode,
  GraphSubgraph,
  GraphSurprisingConnection,
} from "./apiTypes";

export type GraphSubgraphQuery = {
  rootEntityId?: string;
  depth?: number;
  relationTypes?: string[];
  includeCandidates?: boolean;
};

export const DEFAULT_DEFERRAL_DAYS = 7;

const graphRequest = async <T>(request: () => Promise<T>): Promise<T> => {
  try {
    return await request();
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    if (error.status === 403)
      throw new Error("You do not have permission to view this part of the graph.");
    if (error.status === 422)
      throw new Error("That graph query is not valid. Choose a record you can read.");
    throw error;
  }
};

const graphNode = (value: unknown): GraphNode => {
  const item = rec(value);
  return {
    id: str(item.id),
    label: str(item.label, "Untitled record"),
    kind: str(item.kind, "unknown"),
    domainId: str(item.domain_id),
    definition: str(item.definition),
    degree: num(item.degree),
    communityId: str(item.community_id),
    ontologyVersion: str(item.ontology_version),
  };
};

const graphEdge = (value: unknown): GraphEdge => {
  const item = rec(value);
  return {
    id: str(item.id),
    sourceId: str(item.source_id),
    targetId: str(item.target_id),
    relationType: str(item.relation_type, "related_to"),
    confidence: str(item.confidence, "medium"),
    status: str(item.status, "verified"),
    validFrom: str(item.valid_from),
    validTo: str(item.valid_to),
    quotes: asArray<string>(item.quotes).filter(Boolean),
    ideaBlockIds: asArray<string>(item.idea_block_ids).filter(Boolean),
  };
};

const graphCommunity = (value: unknown): GraphCommunity => {
  const item = rec(value);
  return {
    communityId: str(item.community_id),
    memberIds: asArray<string>(item.member_ids),
    internalEdgeCount: num(item.internal_edge_count),
    boundaryEdgeCount: num(item.boundary_edge_count),
    cohesion: num(item.cohesion),
    meanIntraDegree: num(item.mean_intra_degree),
  };
};

const surprisingConnection = (value: unknown): GraphSurprisingConnection => {
  const item = rec(value);
  return {
    edgeId: str(item.edge_id),
    sourceId: str(item.source_id),
    targetId: str(item.target_id),
    score: num(item.score),
    reasons: asArray<string>(item.reasons).filter(Boolean),
  };
};

const knowledgeGap = (value: unknown): GraphKnowledgeGap => {
  const item = rec(value);
  return {
    gapKey: str(item.gap_key),
    gapType: str(item.gap_type),
    title: str(item.title, "Knowledge gap"),
    description: str(item.description),
    nodeIds: asArray<string>(item.node_ids),
    suggestion: str(item.suggestion),
    researchTerm: str(item.research_term),
    researchContext: str(item.research_context),
    affectedRecords: asArray<unknown>(item.affected_records).map((entry) => {
      const record = rec(entry);
      return {
        id: str(record.id),
        label: str(record.label, "Untitled record"),
        kind: str(record.kind, "unknown"),
      };
    }),
  };
};

const subgraphQuery = (query: GraphSubgraphQuery): string => {
  const params = new URLSearchParams();
  if (query.rootEntityId) params.set("root_entity_id", query.rootEntityId);
  if (query.depth) params.set("depth", String(query.depth));
  if (query.includeCandidates) params.set("include_candidates", "true");
  (query.relationTypes ?? []).forEach((value) => params.append("relation_types", value));
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
};

export const graphClient = {
  subgraph: (query: GraphSubgraphQuery = {}): Promise<GraphSubgraph> =>
    graphRequest(async () => {
      const payload = rec(
        await apiClient.get<unknown>(`/api/graph/subgraph${subgraphQuery(query)}`)
      );
      const scope = rec(payload.scope);
      return {
        nodes: asArray<unknown>(payload.nodes).map(graphNode),
        edges: asArray<unknown>(payload.edges).map(graphEdge),
        communities: asArray<unknown>(payload.communities).map(graphCommunity),
        capped: bool(payload.capped),
        depthReached: num(payload.depth_reached),
        ontologyVersion: str(payload.ontology_version),
        scope: {
          entityCount: num(scope.entity_count),
          rootEntityId: str(scope.root_entity_id),
          depth: num(scope.depth),
        },
      };
    }),

  insights: (query: GraphSubgraphQuery = {}): Promise<GraphInsights> =>
    graphRequest(async () => {
      const payload = rec(
        await apiClient.get<unknown>(`/api/graph/insights${subgraphQuery(query)}`)
      );
      return {
        surprisingConnections: asArray<unknown>(payload.surprising_connections).map(
          surprisingConnection
        ),
        knowledgeGaps: asArray<unknown>(payload.knowledge_gaps).map(knowledgeGap),
        hiddenCount: num(payload.hidden_count),
        ontologyVersion: str(payload.ontology_version),
      };
    }),

  decide: (
    targetId: string,
    targetType: "connection" | "gap",
    decision: "dismiss" | "defer",
    reason?: string,
    deferDays: number = DEFAULT_DEFERRAL_DAYS
  ): Promise<unknown> =>
    graphRequest(() =>
      apiClient.post<unknown>("/api/graph/insights/decisions", {
        target_id: targetId,
        target_type: targetType,
        decision,
        reason: reason ?? null,
        defer_days: deferDays,
      })
    ),
};
