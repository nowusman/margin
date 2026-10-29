import React from "react";

type CitationInlineProps = {
  citationId?: string;
  label: string;
  active?: boolean;
  disabled?: boolean;
  onClick?: () => void;
};

export function CitationInline({
  citationId,
  label,
  active = false,
  disabled = false,
  onClick,
}: CitationInlineProps): React.JSX.Element {
  return (
    <button
      className="citation-inline"
      data-citation-id={citationId}
      aria-pressed={active}
      aria-label={disabled ? `${label} (restricted)` : label}
      disabled={disabled}
      type="button"
      onClick={disabled ? undefined : onClick}
    >
      [{label}]
    </button>
  );
}
