import React, { useEffect, useState } from "react";
import { adminClient } from "../../lib/apiAdminSources";
import { governanceClient } from "../../lib/apiGovernance";
import type { SemanticReviewAction } from "../../lib/apiTypes";
import { confirmGovernedAction, shortId } from "../../lib/display";
import { useCommand, useLoad } from "../../lib/hooks";
import { GovernanceReviewPage } from "./GovernanceReviewPage";

export type GovernanceReviewContainerProps = {
  params: Record<string, string>;
  onNavigate: (_route: "/governance/review/:taskId", _params: { taskId: string }) => void;
};

export function GovernanceReviewContainer({
  params,
  onNavigate,
}: GovernanceReviewContainerProps): React.JSX.Element {
  const command = useCommand();
  const [note, setNote] = useState("");
  const [assignedTo, setAssignedTo] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const [reviewTaskType, setReviewTaskType] = useState("");
  const [reviewDomainId, setReviewDomainId] = useState("");
  const [reviewPriority, setReviewPriority] = useState("");
  const [activeTaskId, setActiveTaskId] = useState("");
  const [entityResolutionTargets, setEntityResolutionTargets] = useState<Record<string, string>>(
    {}
  );
  const [semanticSelection, setSemanticSelection] = useState("");
  const review = useLoad(`gov-review-${reviewTaskType}-${reviewDomainId}-${reviewPriority}`, () =>
    governanceClient.reviewTasks({
      taskType: reviewTaskType || undefined,
      domainId: reviewDomainId || undefined,
      priority: reviewPriority || undefined,
    })
  );
  const audit = useLoad("gov-audit", adminClient.auditEvents);
  const selectedTaskId = params.taskId || activeTaskId;
  const reviewDetail = useLoad(
    `gov-review-detail-${selectedTaskId || "none"}`,
    () => governanceClient.reviewTaskDetail(selectedTaskId),
    { enabled: Boolean(selectedTaskId) }
  );
  const reviewBundle = useLoad(
    `gov-review-bundle-${selectedTaskId || "none"}`,
    () => governanceClient.reviewBundle(selectedTaskId),
    { enabled: Boolean(selectedTaskId) }
  );

  useEffect(() => {
    const defaults = Object.fromEntries(
      (reviewBundle.data?.entity_resolutions ?? [])
        .filter((item) => item.status === "review_required" && item.candidates.length > 0)
        .map((item) => [item.mention_id, item.candidates[0].id])
    );
    setEntityResolutionTargets(defaults);
    setSemanticSelection("");
  }, [reviewBundle.data]);

  const runReviewUpdate = (
    label: string,
    payload: { status?: string; assigned_to?: string; resolution?: string; comment?: string },
    requireNote: boolean
  ): void => {
    if (!selectedTaskId) return;
    if (requireNote && !note.trim()) {
      setValidationError("This action requires a note.");
      return;
    }
    if (payload.assigned_to && !payload.assigned_to.trim()) {
      setValidationError("Assignment requires a user.");
      return;
    }
    if (
      !confirmGovernedAction(
        `${label} for "${reviewDetail.data?.title ?? `review ${shortId(selectedTaskId)}`}"`
      )
    ) {
      return;
    }
    setValidationError(null);
    command.runCommand(label, async () => {
      await governanceClient.updateReviewTask(selectedTaskId, {
        ...payload,
        comment: payload.comment ?? (note.trim() || undefined),
        resolution: payload.resolution ?? (note.trim() || undefined),
      });
      review.refresh();
      reviewDetail.refresh();
      audit.refresh();
    });
  };

  const runReviewDecision = (decision: "approved" | "rejected"): void => {
    if (!selectedTaskId) return;
    if (!note.trim()) {
      setValidationError("Approval and rejection require a resolution note.");
      return;
    }
    if (decision === "approved" && reviewBundle.data?.task.type === "entity_resolution_review") {
      setValidationError("Resolve entity mentions before approving an entity-resolution review.");
      return;
    }
    const unresolvedMentions = (reviewBundle.data?.entity_resolutions ?? []).filter(
      (item) => item.status === "review_required" && !entityResolutionTargets[item.mention_id]
    );
    if (decision === "approved" && unresolvedMentions.length > 0) {
      setValidationError("Select a target for every uncertain entity resolution before approval.");
      return;
    }
    if (!confirmGovernedAction(`${decision} the complete evidence bundle`)) return;
    setValidationError(null);
    command.runCommand(`${decision} review bundle`, async () => {
      await governanceClient.decideReviewBundle(selectedTaskId, {
        decision,
        resolution: note.trim(),
        entity_resolution_targets: decision === "approved" ? entityResolutionTargets : {},
      });
      review.refresh();
      reviewDetail.refresh();
      reviewBundle.refresh();
      audit.refresh();
    });
  };

  const runSemanticReviewAction = (action: SemanticReviewAction): void => {
    if (!selectedTaskId) return;
    if (!note.trim()) {
      setValidationError("Semantic review actions require a resolution note.");
      return;
    }
    const pair = reviewBundle.data?.current_statements ?? [];
    const selected = pair.find((item) => item.idea_block_id === semanticSelection);
    if (action === "confirm_equivalent" && !selected) {
      setValidationError("Select the canonical survivor before confirming equivalence.");
      return;
    }
    if ((action === "mark_refinement" || action === "mark_supersession") && !selected) {
      setValidationError("Select the source IdeaBlock before recording a directional relation.");
      return;
    }
    const actionLabel = action.replace(/_/g, " ");
    if (!confirmGovernedAction(`${actionLabel} for the selected review task`)) return;
    const target = pair.find((item) => item.idea_block_id !== semanticSelection);
    setValidationError(null);
    command.runCommand(`${actionLabel} review`, async () => {
      await governanceClient.applyReviewAction(selectedTaskId, {
        action,
        note: note.trim(),
        expected_updated_at: reviewBundle.data?.task.updated_at,
        ...(action === "confirm_equivalent" ? { survivor_id: selected?.idea_block_id } : {}),
        ...(action === "mark_refinement" || action === "mark_supersession"
          ? {
              source_idea_block_id: selected?.idea_block_id,
              target_idea_block_id: target?.idea_block_id,
            }
          : {}),
        ...(action === "mark_contradiction"
          ? {
              source_idea_block_id: selected?.idea_block_id,
              target_idea_block_id: target?.idea_block_id,
            }
          : {}),
      });
      review.refresh();
      reviewDetail.refresh();
      reviewBundle.refresh();
      audit.refresh();
    });
  };

  const filteredAudit = (audit.data ?? []).filter(
    (event) => selectedTaskId && event.target.includes(selectedTaskId)
  );
  return (
    <GovernanceReviewPage
      tasks={review.data}
      tasksLoading={review.loading}
      tasksError={review.error}
      taskType={reviewTaskType}
      domainId={reviewDomainId}
      priority={reviewPriority}
      onTaskTypeChange={setReviewTaskType}
      onDomainIdChange={setReviewDomainId}
      onPriorityChange={setReviewPriority}
      onOpenTask={(taskId) => {
        setActiveTaskId(taskId);
        onNavigate("/governance/review/:taskId", { taskId });
      }}
      onDetectContradictions={() =>
        command.runCommand("Detect contradictions", governanceClient.detectContradictions)
      }
      onDetectBlindSpots={() =>
        command.runCommand("Detect blind spots", governanceClient.detectBlindSpots)
      }
      onCreateLintTasks={() =>
        command.runCommand("Create lint review tasks", governanceClient.createLintReviewTasks)
      }
      detail={reviewDetail.data}
      detailLoading={reviewDetail.loading}
      detailError={reviewDetail.error}
      bundle={reviewBundle.data}
      bundleError={reviewBundle.error}
      entityResolutionTargets={entityResolutionTargets}
      selectedTaskId={selectedTaskId}
      assignedTo={assignedTo}
      note={note}
      validationError={validationError}
      auditEvents={filteredAudit}
      busy={command.busy}
      commandMessage={command.message}
      onAssignedToChange={setAssignedTo}
      onNoteChange={setNote}
      onEntityResolutionTargetChange={(mentionId, entityId) =>
        setEntityResolutionTargets((current) => ({ ...current, [mentionId]: entityId }))
      }
      semanticSelection={semanticSelection}
      onSemanticSelectionChange={setSemanticSelection}
      onSemanticAction={runSemanticReviewAction}
      onDecision={runReviewDecision}
      onAssign={() => runReviewUpdate("Assign review", { assigned_to: assignedTo }, false)}
      onRequestEvidence={() =>
        runReviewUpdate("Request evidence", { status: "needs_evidence" }, true)
      }
    />
  );
}
