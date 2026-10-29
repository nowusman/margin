import React, { useEffect, useRef } from "react";
import { HeatmapChart as EchartsHeatmapChart } from "echarts/charts";
import { GridComponent, TooltipComponent, VisualMapComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import type { HeatmapDomain } from "../../lib/apiTypes";

echarts.use([
  EchartsHeatmapChart,
  GridComponent,
  TooltipComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

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

type HeatmapChartProps = {
  data: HeatmapDomain[];
  metric: HeatmapMetric;
  onSelectDomain?: (_domainId: string) => void;
};

const metricLabel: Record<HeatmapMetric, string> = {
  healthScore: "Health",
  staleClaims: "Stale",
  highSeverityRisks: "High Risk",
  atdi: "ATDI",
  debt: "Technical debt",
  expiredWaivers: "Expired Waivers",
  unownedEntities: "Unowned",
  missingAdrCoverage: "Missing ADR",
  missingDrSecurityEvidence: "Missing DR/Security",
};

const problemMetricColors = ["#2f8f61", "#f5edb8", "#bd4651"];
const healthMetricColors = ["#bd4651", "#f5edb8", "#2f8f61"];

export function HeatmapChart({
  data,
  metric,
  onSelectDomain,
}: HeatmapChartProps): React.JSX.Element {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current);
    const domains = data.map((item) => item.name);
    const values = data.map((item, index) => [index, 0, item[metric] ?? 0]);
    const valueMax = Math.max(1, ...values.map(([, , value]) => Number(value)));
    const visualMax = metric === "healthScore" ? 100 : valueMax;

    chart.setOption({
      tooltip: { position: "top" },
      grid: { top: 20, left: 20, right: 20, bottom: 40, containLabel: true },
      xAxis: { type: "category", data: domains, axisLabel: { rotate: 25 } },
      yAxis: { type: "category", data: [metricLabel[metric]] },
      visualMap: {
        min: 0,
        max: visualMax,
        calculable: false,
        orient: "horizontal",
        left: "center",
        bottom: 0,
        inRange: {
          color: metric === "healthScore" ? healthMetricColors : problemMetricColors,
        },
      },
      series: [
        {
          type: "heatmap",
          data: values,
          label: { show: true },
        },
      ],
    });

    const clickHandler = (event: { data?: unknown }) => {
      if (!onSelectDomain || !Array.isArray(event.data)) return;
      const xIndex = Number(event.data[0]);
      if (!Number.isFinite(xIndex)) return;
      const selected = data[xIndex];
      if (selected) onSelectDomain(selected.id);
    };
    chart.on("click", clickHandler);

    return () => {
      chart.off("click", clickHandler);
      chart.dispose();
    };
  }, [data, metric, onSelectDomain]);

  return <div className="heatmap-chart" ref={ref} />;
}
