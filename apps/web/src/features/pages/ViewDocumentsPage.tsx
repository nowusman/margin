import React, { useEffect, useMemo, useState } from "react";
import { EmptyState, ErrorState, LoadingState } from "../../components/layout/States";
import { sourcesClient } from "../../lib/apiAdminSources";
import type { SourceSummary } from "../../lib/apiTypes";
import { shortId } from "../../lib/display";
import { useLoad } from "../../lib/hooks";
import { AppRoute } from "./types";

type ViewDocumentsPageProps = {
  params: Record<string, string>;
  onNavigate: (_route: AppRoute, _params?: Record<string, string>) => void;
};

const extensionFromSource = (source: SourceSummary): string => {
  const values = [source.title, source.sourceUri].filter(Boolean);
  for (const value of values) {
    const match = value.toLowerCase().match(/\.([a-z0-9]+)(?:$|[?#])/);
    if (match?.[1]) return match[1];
  }
  if (source.mediaType.includes("pdf")) return "pdf";
  if (source.mediaType.includes("presentation")) return "pptx";
  if (source.mediaType.includes("word")) return "docx";
  if (source.mediaType.includes("text")) return "txt";
  return "document";
};

const documentKind = (source: SourceSummary): string => {
  const extension = extensionFromSource(source);
  const labels: Record<string, string> = {
    doc: "Word",
    docx: "Word",
    md: "Text",
    pdf: "PDF",
    ppt: "PowerPoint",
    pptx: "PowerPoint",
    rtf: "Text",
    txt: "Text",
  };
  return labels[extension] ?? extension.toUpperCase();
};

const downloadFilename = (source: SourceSummary): string =>
  source.originalFilename || source.sourceUri || source.title;

const canRenderInBrowser = (source: SourceSummary): boolean => {
  const mediaType = source.mediaType.toLowerCase();
  if (mediaType === "application/pdf") return true;
  if (mediaType.startsWith("image/")) return true;
  if (mediaType.startsWith("application/vnd.margin.")) return true;
  if (mediaType === "text/plain" || mediaType === "text/markdown") return true;
  return false;
};

const hasPdfPreview = (source: SourceSummary): boolean => {
  const mediaType = source.mediaType.toLowerCase();
  return (
    mediaType.includes("wordprocessingml.document") ||
    mediaType.includes("presentationml.presentation")
  );
};

const selectedOrFirstSourceId = (sources: SourceSummary[], requestedSourceId: string): string => {
  if (requestedSourceId && sources.some((source) => source.id === requestedSourceId))
    return requestedSourceId;
  return sources[0]?.id ?? requestedSourceId;
};

const useObjectUrl = (blob: Blob | null): string => {
  const [url, setUrl] = useState("");

  useEffect(() => {
    if (!blob) {
      setUrl("");
      return undefined;
    }
    const nextUrl = URL.createObjectURL(blob);
    setUrl(nextUrl);
    return () => URL.revokeObjectURL(nextUrl);
  }, [blob]);

  return url;
};

export function ViewDocumentsPage({
  params,
  onNavigate,
}: ViewDocumentsPageProps): React.JSX.Element {
  const sources = useLoad("view-documents-sources", sourcesClient.list);
  const selectedSourceId = selectedOrFirstSourceId(sources.data ?? [], params.sourceId || "");
  const selectedSource = useMemo(
    () => (sources.data ?? []).find((source) => source.id === selectedSourceId) ?? null,
    [selectedSourceId, sources.data]
  );
  const previewMode = selectedSource && hasPdfPreview(selectedSource) ? "pdf-preview" : "original";
  const sourceFile = useLoad(
    `view-document-file-${previewMode}-${selectedSourceId}`,
    (signal) =>
      selectedSource && hasPdfPreview(selectedSource)
        ? sourcesClient.previewFile(selectedSourceId, signal)
        : sourcesClient.file(selectedSourceId, signal),
    { enabled: Boolean(selectedSource) }
  );
  const objectUrl = useObjectUrl(sourceFile.data);
  const canPreview = Boolean(
    selectedSource &&
      objectUrl &&
      (canRenderInBrowser(selectedSource) || hasPdfPreview(selectedSource))
  );

  return (
    <section className="view-documents-page">
      <article className="card document-nav-card">
        <div className="document-card-header">
          <div>
            <p className="eyebrow">Source Documents</p>
            <h2>View Documents</h2>
          </div>
          <span className="badge tone-info">{sources.data?.length ?? 0}</span>
        </div>
        {sources.loading ? <LoadingState label="Loading documents..." /> : null}
        <ErrorState error={sources.error} />
        {(sources.data ?? []).length > 0 ? (
          <div className="document-nav-list" role="listbox" aria-label="Uploaded source documents">
            {(sources.data ?? []).map((source) => (
              <button
                type="button"
                key={source.id}
                className={
                  source.id === selectedSourceId ? "document-nav-item active" : "document-nav-item"
                }
                aria-selected={source.id === selectedSourceId}
                role="option"
                onClick={() => onNavigate("/documents/:sourceId", { sourceId: source.id })}
              >
                <span>
                  <strong>{source.title}</strong>
                  <em>{shortId(source.id)}</em>
                </span>
                <span className="badge">{documentKind(source)}</span>
              </button>
            ))}
          </div>
        ) : (
          !sources.loading && <EmptyState label="No uploaded source documents." />
        )}
      </article>
      <article className="card document-reader-card">
        {selectedSource ? (
          <div className="document-reader-header">
            <div>
              <p className="eyebrow">{documentKind(selectedSource)}</p>
              <h2>{selectedSource.title}</h2>
              <p className="subtle">
                {selectedSource.status || "unknown"} - {selectedSource.mediaType || "unknown type"}
              </p>
            </div>
            <span className="badge tone-info">{selectedSource.sensitivity || "internal"}</span>
          </div>
        ) : null}
        {sourceFile.loading ? <LoadingState label="Opening document..." /> : null}
        <ErrorState error={sourceFile.error} />
        {!selectedSource && !sources.loading ? (
          <EmptyState label="Select a document to open." />
        ) : null}
        {selectedSource && !sourceFile.loading && canPreview ? (
          <div className="document-reader" aria-label={`${selectedSource.title} content`}>
            <iframe src={objectUrl} title={`${selectedSource.title} preview`} />
          </div>
        ) : null}
        {selectedSource && !sourceFile.loading && objectUrl && !canPreview && !sourceFile.error ? (
          <div className="empty-state">
            <p>This file type cannot be previewed in the browser.</p>
            <a className="button-link" href={objectUrl} download={downloadFilename(selectedSource)}>
              Open original document
            </a>
          </div>
        ) : null}
        {selectedSource && !sourceFile.loading && !objectUrl && !sourceFile.error ? (
          <EmptyState label="Original document is not available." />
        ) : null}
      </article>
    </section>
  );
}
