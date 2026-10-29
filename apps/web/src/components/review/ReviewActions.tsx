import React from "react";
import type { SemanticReviewAction } from "../../lib/apiTypes";

type ReviewActionsProps = {
  disabled?: boolean;
  onAction: (_action: SemanticReviewAction) => void;
};

const actions: Array<{ action: SemanticReviewAction; label: string }> = [
  { action: "confirm_equivalent", label: "Confirm equivalent" },
  { action: "approve_distinct", label: "Approve distinct" },
  { action: "mark_refinement", label: "Mark refinement" },
  { action: "mark_contradiction", label: "Mark contradiction" },
  { action: "mark_supersession", label: "Mark supersession" },
];

export function ReviewActions({ disabled, onAction }: ReviewActionsProps): React.JSX.Element {
  return (
    <div className="actions-row" aria-label="Semantic review actions">
      {actions.map(({ action, label }) => (
        <button key={action} type="button" disabled={disabled} onClick={() => onAction(action)}>
          {label}
        </button>
      ))}
    </div>
  );
}
