import React, { useEffect, useMemo, useRef } from "react";
import { GraphChart } from "echarts/charts";
import { LegendComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import type { GraphEdge, GraphNode } from "../../lib/apiTypes";
import { rec, str } from "../../lib/apiMappers";

echarts.use([GraphChart, TooltipComponent, LegendComponent, CanvasRenderer]);

const COMMUNITY_COLORS = [
  "#2f8f61",
  "#3f6fb5",
  "#b5543f",
  "#7a5ea8",
  "#b58a2f",
  "#3f8a9a",
  "#a04f7a",
  "#6b7a2f",
];

export type GraphMapProps = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  onSelectNode?: (_nodeId: string) => void;
  onSelectEdge?: (_edge: GraphEdge) => void;
};

const communityColor = (communityId: string): string => {
  if (!communityId) return "#6b7280";
  let hash = 0;
  for (let position = 0; position < communityId.length; position += 1) {
    hash = (hash * 31 + communityId.charCodeAt(position)) % COMMUNITY_COLORS.length;
  }
  return COMMUNITY_COLORS[hash];
};

export function GraphMap({
  nodes,
  edges,
  onSelectNode,
  onSelectEdge,
}: GraphMapProps): React.JSX.Element {
  const ref = useRef<HTMLDivElement | null>(null);

  const communityIds = useMemo(() => {
    const seen: string[] = [];
    nodes.forEach((node) => {
      if (node.communityId && !seen.includes(node.communityId)) seen.push(node.communityId);
    });
    return seen;
  }, [nodes]);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    const degreeByNode = new Map<string, number>();
    nodes.forEach((node) => degreeByNode.set(node.id, node.degree));

    chart.setOption({
      tooltip: { confine: true },
      legend: [{ data: communityIds }],
      series: [
        {
          type: "graph",
          layout: "force",
          roam: true,
          draggable: true,
          categories: communityIds.map((communityId) => ({
            name: communityId,
            itemStyle: { color: communityColor(communityId) },
          })),
          force: { repulsion: 220, edgeLength: [60, 160], gravity: 0.08 },
          label: { show: true, position: "right" },
          edgeSymbol: ["none", "arrow"],
          edgeSymbolSize: 7,
          edgeLabel: { show: false },
          emphasis: { focus: "adjacency", label: { show: true } },
          data: nodes.map((node) => {
            const category = node.communityId ? communityIds.indexOf(node.communityId) : -1;
            return {
              id: node.id,
              name: node.label,
              // A record that belongs to no community has no category to sit
              // in, so it is drawn in the neutral colour instead.
              ...(category >= 0 ? { category } : {}),
              value: degreeByNode.get(node.id) ?? 0,
              symbolSize: 12 + Math.min(24, (degreeByNode.get(node.id) ?? 0) * 4),
              itemStyle: {
                color: communityColor(node.communityId),
              },
            };
          }),
          links: edges.map((edge) => ({
            id: edge.id,
            source: edge.sourceId,
            target: edge.targetId,
            value: edge.relationType,
          })),
        },
      ],
    });

    const clickHandler = (event: { dataType?: string; data?: unknown }): void => {
      const data = rec(event.data as unknown);
      if (event.dataType === "node") {
        const id = str(data.id);
        if (id && onSelectNode) onSelectNode(id);
        return;
      }
      if (event.dataType === "edge") {
        const id = str(data.id);
        const edge = edges.find((item) => item.id === id);
        if (edge && onSelectEdge) onSelectEdge(edge);
      }
    };
    chart.on("click", clickHandler);

    return () => {
      chart.off("click", clickHandler);
      chart.dispose();
    };
  }, [nodes, edges, communityIds, onSelectNode, onSelectEdge]);

  return (
    <div className="graph-map">
      <div className="graph-map-canvas" ref={ref} data-testid="graph-map-canvas" />
      <ul className="graph-map-records" data-testid="graph-map-records">
        {nodes.map((node) => (
          <li key={node.id} data-testid="graph-map-node" data-node-id={node.id}>
            <strong>{node.label}</strong>
            <span className="subtle">
              {node.kind} · {node.degree} connection{node.degree === 1 ? "" : "s"}
              {node.communityId ? ` · ${node.communityId}` : ""}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
