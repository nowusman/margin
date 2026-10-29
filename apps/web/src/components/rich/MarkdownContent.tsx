import React, { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import "katex/dist/katex.min.css";

export type MarkdownContentProps = {
  content: string;
  className?: string;
};

type MermaidBlockProps = {
  chart: string;
};

let mermaidDiagramCount = 0;

const MermaidBlock = ({ chart }: MermaidBlockProps): React.JSX.Element => {
  const ref = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const render = async (): Promise<void> => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
        mermaidDiagramCount += 1;
        const { svg } = await mermaid.render(`margin-mermaid-${mermaidDiagramCount}`, chart);
        if (cancelled || !ref.current) return;
        ref.current.innerHTML = svg;
        setError(null);
      } catch (reason) {
        if (!cancelled)
          setError(reason instanceof Error ? reason.message : "Diagram failed to render");
      }
    };
    void render();
    return () => {
      cancelled = true;
    };
  }, [chart]);

  return (
    <div className="markdown-diagram" data-testid="markdown-diagram">
      {error ? <p className="state-error">{error}</p> : null}
      <div ref={ref} />
    </div>
  );
};

export function MarkdownContent({ content, className }: MarkdownContentProps): React.JSX.Element {
  const components = useMemo(
    () => ({
      code({
        className: codeClassName,
        children,
      }: {
        className?: string;
        children?: React.ReactNode;
      }) {
        const language = /language-([a-z0-9]+)/i.exec(codeClassName ?? "")?.[1]?.toLowerCase();
        const text = String(children ?? "").replace(/\n$/, "");
        if (language === "mermaid") {
          return <MermaidBlock chart={text} />;
        }
        return <code className={codeClassName}>{children}</code>;
      },
    }),
    []
  );

  return (
    <div className={className ? `markdown-content ${className}` : "markdown-content"}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
