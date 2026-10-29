import React, { useEffect, useMemo, useState } from "react";
import type { DiagramSummary } from "../../lib/apiTypes";
import { apiClient } from "../../lib/api-client";

type DiagramPanelProps = {
  diagram: DiagramSummary | null;
};

type RenderState = {
  svg: string;
  error: string;
  loading: boolean;
};

export function DiagramPanel({ diagram }: DiagramPanelProps): React.JSX.Element {
  const [state, setState] = useState<RenderState>({ svg: "", error: "", loading: false });
  const renderId = useMemo(
    () => `margin-diagram-${diagram?.id.replace(/[^a-zA-Z0-9_-]/g, "-") ?? "empty"}`,
    [diagram?.id]
  );

  useEffect(() => {
    let cancelled = false;
    const render = async (): Promise<void> => {
      if (!diagram) {
        setState({ svg: "", error: "", loading: false });
        return;
      }
      if (diagram.svg) {
        setState({ svg: diagram.svg, error: "", loading: false });
        return;
      }
      if (diagram.type === "structurizr") {
        setState({
          svg: "",
          error:
            "Structurizr source is parsed for governed entities and relationships; rendering is not enabled.",
          loading: false,
        });
        return;
      }
      setState({ svg: "", error: "", loading: true });
      try {
        if (diagram.type === "mermaid") {
          const mermaid = await import("mermaid");
          mermaid.default.initialize({ securityLevel: "strict", startOnLoad: false });
          const { svg } = await mermaid.default.render(renderId, diagram.markup ?? "");
          if (!cancelled) setState({ svg, error: "", loading: false });
          return;
        }
        const svg = await apiClient.get<string>(
          `/api/diagrams/${encodeURIComponent(diagram.id)}/render`,
          {
            headers: { Accept: "image/svg+xml" },
            timeoutMs: 30_000,
          }
        );
        if (!cancelled) setState({ svg, error: "", loading: false });
      } catch (error) {
        if (!cancelled) {
          setState({
            svg: "",
            error: error instanceof Error ? error.message : "Diagram renderer is not configured.",
            loading: false,
          });
        }
      }
    };
    void render();
    return () => {
      cancelled = true;
    };
  }, [diagram, renderId]);

  if (!diagram) return <p className="subtle">No diagram selected.</p>;

  return (
    <section className="diagram-panel">
      <h3>{diagram.title}</h3>
      <p>Type: {diagram.type}</p>
      {state.loading ? <p className="subtle">Rendering diagram...</p> : null}
      {state.svg ? (
        <div
          className="diagram-canvas"
          role="img"
          aria-label={`${diagram.title} ${diagram.type} diagram`}
          dangerouslySetInnerHTML={{ __html: state.svg }}
        />
      ) : (
        <>
          {state.error ? <p className="subtle">{state.error}</p> : null}
          <pre className="diagram-text">{diagram.markup ?? "Diagram markup not available."}</pre>
        </>
      )}
    </section>
  );
}
