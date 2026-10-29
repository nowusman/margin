import React from "react";
import { TextAreaField, TextField } from "../../components/forms";
import { EmptyState, ErrorState, LoadingState } from "../../components/layout/States";
import { ReviewActions } from "../../components/review";
import { DataTable } from "../../components/tables";
import type {
  AuditEvent,
  ReviewBundle,
  ReviewQueueItem,
  SemanticReviewAction,
} from "../../lib/apiTypes";
import { shortId, technicalIdLabel } from "../../lib/display";

type Props = {
  tasks: ReviewQueueItem[] | null;
  tasksLoading: boolean;
  tasksError: string | null;
  taskType: string;
  domainId: string;
  priority: string;
  onTaskTypeChange: (_value: string) => void;
  onDomainIdChange: (_value: string) => void;
  onPriorityChange: (_value: string) => void;
  onOpenTask: (_taskId: string) => void;
  onDetectContradictions: () => void;
  onDetectBlindSpots: () => void;
  onCreateLintTasks: () => void;
  detail: Pick<
    ReviewQueueItem,
    "id" | "title" | "comments" | "evidenceId" | "proposedChange"
  > | null;
  detailLoading: boolean;
  detailError: string | null;
  bundle: ReviewBundle | null;
  bundleError: string | null;
  entityResolutionTargets: Record<string, string>;
  selectedTaskId: string;
  assignedTo: string;
  note: string;
  validationError: string | null;
  auditEvents: AuditEvent[];
  busy: boolean;
  commandMessage: string | null;
  onAssignedToChange: (_value: string) => void;
  onNoteChange: (_value: string) => void;
  onEntityResolutionTargetChange: (_mentionId: string, _entityId: string) => void;
  semanticSelection: string;
  onSemanticSelectionChange: (_value: string) => void;
  onSemanticAction: (_action: SemanticReviewAction) => void;
  onDecision: (_decision: "approved" | "rejected") => void;
  onAssign: () => void;
  onRequestEvidence: () => void;
};

export function GovernanceReviewPage(props: Props): React.JSX.Element {
  return (
    <section className="page-grid">
      <article className="card">
        <h2>Review queue</h2>
        <div className="filter-grid">
          <TextField label="Task type" value={props.taskType} onChange={props.onTaskTypeChange} />
          <TextField label="Domain" value={props.domainId} onChange={props.onDomainIdChange} />
          <TextField label="Priority" value={props.priority} onChange={props.onPriorityChange} />
        </div>
        {props.tasksLoading ? <LoadingState label="Loading review tasks..." /> : null}
        <ErrorState error={props.tasksError} />
        {props.tasks && props.tasks.length > 0 ? (
          <DataTable
            rows={props.tasks}
            emptyLabel="No review tasks."
            getRowKey={(row) => row.id}
            columns={[
              { header: "Task", render: (row) => row.title, sortValue: (row) => row.title },
              {
                header: "Priority",
                render: (row) => row.priority,
                sortValue: (row) => row.priority,
              },
              { header: "Status", render: (row) => row.status, sortValue: (row) => row.status },
              {
                header: "Evidence",
                render: (row) => `Claim ${shortId(row.evidenceId)}`,
                sortValue: (row) => row.evidenceId,
              },
              {
                header: "Action",
                render: (row) => (
                  <button type="button" onClick={() => props.onOpenTask(row.id)}>
                    Open
                  </button>
                ),
              },
            ]}
          />
        ) : (
          !props.tasksLoading && <EmptyState label="No review tasks available." />
        )}
        <p className="subtle">
          Synthesis candidates and inferred knowledge stay in review until approved.
        </p>
        <div className="actions-row">
          <button type="button" disabled={props.busy} onClick={props.onDetectContradictions}>
            Detect contradictions
          </button>
          <button type="button" disabled={props.busy} onClick={props.onDetectBlindSpots}>
            Detect blind spots
          </button>
          <button type="button" disabled={props.busy} onClick={props.onCreateLintTasks}>
            Create lint review tasks
          </button>
        </div>
      </article>

      <article className="card">
        <h2>Review task detail</h2>
        {props.detailLoading ? <LoadingState label="Loading review detail..." /> : null}
        <ErrorState error={props.detailError} />
        <ErrorState error={props.bundleError} />
        {props.detail ? (
          <>
            <p>Task: {props.detail.title}</p>
            <p>Proposed change: {props.detail.proposedChange}</p>
            <p>Evidence: Claim {shortId(props.detail.evidenceId)}</p>
            {props.bundle ? (
              <EvidenceBundle
                bundle={props.bundle}
                resolutionTargets={props.entityResolutionTargets}
                onResolutionTargetChange={props.onEntityResolutionTargetChange}
                semanticSelection={props.semanticSelection}
                onSemanticSelectionChange={props.onSemanticSelectionChange}
              />
            ) : null}
            <h3>Comments</h3>
            {props.detail.comments.length > 0 ? (
              props.detail.comments.map((comment, index) => (
                <p key={`${index}-${comment}`}>{comment}</p>
              ))
            ) : (
              <EmptyState label="No review comments." />
            )}
            <details className="technical-details">
              <summary>Technical details</summary>
              <p className="technical-id">{technicalIdLabel(props.detail.id)}</p>
              <p className="technical-id">Evidence record: {props.detail.evidenceId}</p>
            </details>
            <TextField
              label="Assign to"
              value={props.assignedTo}
              onChange={props.onAssignedToChange}
              placeholder="reviewer@domain"
            />
            <TextAreaField
              label="Review note / resolution"
              value={props.note}
              onChange={props.onNoteChange}
              placeholder="Provide rationale for reject/edit/needs-evidence actions."
              rows={4}
            />
            {props.validationError ? <p role="alert">Error: {props.validationError}</p> : null}
            {props.bundle?.semantic_resolution ? (
              <ReviewActions
                disabled={props.busy || !props.selectedTaskId}
                onAction={props.onSemanticAction}
              />
            ) : (
              <div className="actions-row" aria-label="Review bundle actions">
                {props.bundle?.task?.type === "entity_resolution_review" ? (
                  <p role="status">
                    Resolve entity mentions before approving. Generic approve is blocked for entity
                    resolution review.
                  </p>
                ) : (
                  <button
                    type="button"
                    disabled={props.busy || !props.selectedTaskId}
                    onClick={() => props.onDecision("approved")}
                  >
                    {props.bundle?.task?.target_type === "idea_block_relation"
                      ? "Confirm contradiction"
                      : "Approve bundle"}
                  </button>
                )}
                <button
                  type="button"
                  disabled={props.busy || !props.selectedTaskId}
                  onClick={() => props.onDecision("rejected")}
                >
                  {props.bundle?.task?.target_type === "idea_block_relation"
                    ? "Dismiss contradiction"
                    : "Reject bundle"}
                </button>
              </div>
            )}
            <button
              type="button"
              disabled={props.busy || !props.selectedTaskId}
              onClick={props.onAssign}
            >
              Assign reviewer
            </button>
            <button
              type="button"
              disabled={props.busy || !props.selectedTaskId}
              onClick={props.onRequestEvidence}
            >
              Request evidence
            </button>
            <h3>Audit / provenance context</h3>
            {props.auditEvents.length > 0 ? (
              props.auditEvents.map((event) => (
                <p key={event.id}>
                  {event.actor} - {event.action} - {event.at}
                </p>
              ))
            ) : (
              <EmptyState label="No audit records for this task yet." />
            )}
          </>
        ) : (
          !props.detailLoading && <EmptyState label="Select a review task to inspect details." />
        )}
        {props.commandMessage ? <p role="status">{props.commandMessage}</p> : null}
      </article>
    </section>
  );
}

function EvidenceBundle({
  bundle,
  resolutionTargets,
  onResolutionTargetChange,
  semanticSelection,
  onSemanticSelectionChange,
}: {
  bundle: ReviewBundle;
  resolutionTargets: Record<string, string>;
  onResolutionTargetChange: (_mentionId: string, _entityId: string) => void;
  semanticSelection: string;
  onSemanticSelectionChange: (_value: string) => void;
}): React.JSX.Element {
  return (
    <>
      <h3>Evidence bundle</h3>
      {bundle.claims.map((claim) => (
        <section key={claim.id} className="review-evidence">
          <p>Claim: {claim.statement}</p>
          <p>
            Revision: {claim.revision_id} ({claim.status})
          </p>
          {claim.evidence.map((evidence) => (
            <div key={evidence.id}>
              <blockquote>{evidence.source_quote}</blockquote>
              <p>
                Source version: {evidence.source_version?.id ?? "Unknown"} (
                {evidence.source_version?.status ?? "unknown"})
              </p>
              <p>
                Section:{" "}
                {evidence.context.section_path || evidence.context.section_heading || "Unknown"}
              </p>
              {evidence.context.before ? <p>Before: {evidence.context.before}</p> : null}
              {evidence.context.after ? <p>After: {evidence.context.after}</p> : null}
            </div>
          ))}
        </section>
      ))}
      {bundle.current_statements.length === 2 ? (
        <label className="field">
          <span>Canonical survivor / relation source</span>
          <select
            value={semanticSelection}
            onChange={(event) => onSemanticSelectionChange(event.target.value)}
          >
            <option value="">Select an IdeaBlock</option>
            {bundle.current_statements.map((item) => (
              <option key={item.idea_block_id} value={item.idea_block_id}>
                {item.idea_block_id}: {item.revision.statement}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {bundle.suggested_outcome ? <p>Suggested outcome: {bundle.suggested_outcome}</p> : null}
      {Object.keys(bundle.protected_field_differences).length > 0 ? (
        <section>
          <h4>Protected-field differences</h4>
          {Object.entries(bundle.protected_field_differences).map(([field, difference]) => (
            <p key={field}>
              {field}: {JSON.stringify(difference.left)} vs {JSON.stringify(difference.right)}
            </p>
          ))}
        </section>
      ) : null}
      {bundle.revisions.length > 0 ? (
        <p>Historical revisions retained: {bundle.revisions.length}</p>
      ) : null}
      <p>
        Entities:{" "}
        {bundle.entities.map((entity) => `${entity.name} (${entity.kind})`).join(", ") || "None"}
      </p>
      <p>Resolution decisions: {bundle.entity_resolutions.length}</p>
      {bundle.entity_resolutions
        .filter((item) => item.status === "review_required")
        .map((item) => (
          <label className="field" key={item.mention_id}>
            <span>Resolve “{item.mention}” to</span>
            <select
              value={resolutionTargets[item.mention_id] ?? ""}
              onChange={(event) => onResolutionTargetChange(item.mention_id, event.target.value)}
            >
              <option value="">Select an entity</option>
              {item.candidates.map((candidate) => (
                <option key={candidate.id} value={candidate.id}>
                  {candidate.name} ({candidate.kind})
                </option>
              ))}
            </select>
          </label>
        ))}
      <p>
        Explicit relationships:{" "}
        {bundle.relationships.map((item) => item.relation_type).join(", ") || "None"}
      </p>
      {bundle.idea_block_relations.length > 0 ? (
        <section className="review-evidence" aria-label="Idea Block relationship review">
          <h4>Idea Block contradiction</h4>
          {bundle.idea_block_relations.map((relation) => (
            <div key={relation.id}>
              <p>
                {relation.source_idea_block_id} {relation.relation_type}{" "}
                {relation.target_idea_block_id}
              </p>
              <p>
                Review state: {relation.status} · Confidence: {relation.confidence}
              </p>
              <p className="subtle">
                This decision applies only to the relationship. Linked IdeaBlocks and governance
                records keep their current lifecycle state.
              </p>
            </div>
          ))}
        </section>
      ) : null}
      {bundle.governance_records.length > 0 ? (
        <section className="review-evidence" aria-label="Governance records affected">
          <h4>Governance context</h4>
          {bundle.governance_records.map((record) => (
            <p key={record.id}>
              {record.business_key}: {record.title} ({record.record_type}, {record.lifecycle_status}
              )
            </p>
          ))}
        </section>
      ) : null}
      <p>Contradictions: {bundle.contradictions.length}</p>
      <p>
        Unmapped extraction records:{" "}
        {bundle.unmapped_extractions
          .map((item) => String(item.id ?? item.kind ?? "record"))
          .join(", ") || "None"}
      </p>
    </>
  );
}
