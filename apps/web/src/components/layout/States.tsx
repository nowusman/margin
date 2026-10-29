import React from "react";

type LoadingStateProps = {
  label?: string;
};

type ErrorStateProps = {
  error: string | null;
};

type EmptyStateProps = {
  label: string;
  action?: React.ReactNode;
};

export function LoadingState({ label = "Loading..." }: LoadingStateProps): React.JSX.Element {
  return (
    <p className="state-message" role="status">
      {label}
    </p>
  );
}

export function ErrorState({ error }: ErrorStateProps): React.JSX.Element | null {
  if (!error) return null;
  return (
    <p className="state-message state-error" role="alert">
      Error: {error}
    </p>
  );
}

export function EmptyState({ label, action }: EmptyStateProps): React.JSX.Element {
  return (
    <div className="state-message state-empty">
      <p>{label}</p>
      {action}
    </div>
  );
}
