import React from "react";
import { ConfidenceBadge, FreshnessBadge, SensitivityBadge, StatusBadge } from "../badges/Badges";
import { technicalIdLabel } from "../../lib/display";

export type EvidenceCardData = {
  sourceTitle?: string;
  sourceType?: string;
  version?: string;
  pageLabel?: string;
  elementLabel?: string;
  quote?: string;
  ideaBlockId?: string;
  extractionMethod?: string;
  confidence?: string;
  verificationStatus?: string;
  freshness?: string;
  sensitivity?: string;
  permissionsState?: string;
  redacted?: boolean;
  redactionReason?: string;
  reviewer?: string;
  pageCoordinates?: string;
};

type EvidenceCardProps = {
  evidence: EvidenceCardData;
  onOpenSource?: () => void;
  onOpenIdeaBlock?: () => void;
  onReportIssue?: () => void;
  onRequestReview?: () => void;
};

const fallback = (value: string | undefined, label: string): string => value || label;

export function EvidenceCard({
  evidence,
  onOpenSource,
  onOpenIdeaBlock,
  onReportIssue,
  onRequestReview,
}: EvidenceCardProps): React.JSX.Element {
  return (
    <article className="card evidence-card">
      <h3>Evidence</h3>
      {evidence.redacted ? (
        <p className="state-message" role="status">
          Evidence details are restricted by your permissions. The Idea Block remains available, but
          the source quote and source navigation are hidden.
        </p>
      ) : null}
      <dl>
        <dt>Source</dt>
        <dd>{fallback(evidence.sourceTitle, "Unknown source")}</dd>
        <dt>Type / Version</dt>
        <dd>
          {[evidence.sourceType, evidence.version].filter(Boolean).join(" - ") || "Not provided"}
        </dd>
        <dt>Source page</dt>
        <dd>{fallback(evidence.pageLabel, "Source page not provided")}</dd>
        <dt>Source element</dt>
        <dd>{fallback(evidence.elementLabel, "Source element not provided")}</dd>
        <dt>Page / Coordinates</dt>
        <dd>{fallback(evidence.pageCoordinates, "Not provided")}</dd>
        <dt>Reviewer</dt>
        <dd>{fallback(evidence.reviewer, "Pending review")}</dd>
        <dt>Permissions state</dt>
        <dd>{fallback(evidence.permissionsState, "Full access")}</dd>
      </dl>
      <blockquote>
        {fallback(
          evidence.quote,
          evidence.redacted
            ? "The quoted evidence is restricted."
            : "No quoted evidence was returned."
        )}
      </blockquote>
      <div className="badge-row">
        <ConfidenceBadge label={fallback(evidence.confidence, "unknown")} />
        <StatusBadge label={fallback(evidence.verificationStatus, "candidate")} />
        <FreshnessBadge label={fallback(evidence.freshness, "current")} />
        <SensitivityBadge label={fallback(evidence.sensitivity, "internal")} />
      </div>
      <p className="subtle">
        Extraction method: {fallback(evidence.extractionMethod, "deterministic")}
      </p>
      <details className="technical-details">
        <summary>Technical details</summary>
        <p className="technical-id">{technicalIdLabel(evidence.ideaBlockId)}</p>
      </details>
      <div className="actions-row">
        <button type="button" onClick={onOpenSource} disabled={!onOpenSource}>
          Open source
        </button>
        <button type="button" onClick={onOpenIdeaBlock} disabled={!onOpenIdeaBlock}>
          Open IdeaBlock
        </button>
        <button type="button" onClick={onReportIssue} disabled={!onReportIssue}>
          Report issue
        </button>
        <button type="button" onClick={onRequestReview} disabled={!onRequestReview}>
          Request review
        </button>
      </div>
    </article>
  );
}
