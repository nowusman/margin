import React from "react";

export type IconActionName =
  | "add"
  | "edit"
  | "delete"
  | "view"
  | "approve"
  | "reject"
  | "candidate";

function ActionIcon({ name }: { name: IconActionName }): React.JSX.Element {
  const paths: Record<IconActionName, React.JSX.Element> = {
    add: (
      <>
        <path d="M12 5v14" />
        <path d="M5 12h14" />
      </>
    ),
    edit: (
      <>
        <path d="M4 20h4l11-11a2.8 2.8 0 0 0-4-4L4 16v4z" />
        <path d="M13.5 6.5l4 4" />
      </>
    ),
    delete: (
      <>
        <path d="M5 7h14" />
        <path d="M10 11v6" />
        <path d="M14 11v6" />
        <path d="M8 7l1 13h6l1-13" />
        <path d="M9 7l1-3h4l1 3" />
      </>
    ),
    view: (
      <>
        <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6z" />
        <circle cx="12" cy="12" r="2.5" />
      </>
    ),
    approve: (
      <>
        <path d="M20 6 9 17l-5-5" />
      </>
    ),
    reject: (
      <>
        <path d="M18 6 6 18" />
        <path d="M6 6l12 12" />
      </>
    ),
    candidate: (
      <>
        <circle cx="12" cy="12" r="8" />
        <path d="M12 8v5l3 2" />
      </>
    ),
  };

  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      {paths[name]}
    </svg>
  );
}

export function IconAction({
  label,
  icon,
  onClick,
  disabled = false,
}: {
  label: string;
  icon: IconActionName;
  onClick: () => void;
  disabled?: boolean;
}): React.JSX.Element {
  return (
    <button
      type="button"
      className="icon-action"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
    >
      <ActionIcon name={icon} />
    </button>
  );
}
