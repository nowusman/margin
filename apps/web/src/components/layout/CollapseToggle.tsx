import React from "react";

type CollapseToggleProps = {
  expanded: boolean;
  expandedLabel: string;
  collapsedLabel: string;
  expandedDirection?: "up" | "left";
  collapsedDirection?: "down" | "right";
  className?: string;
  onClick: () => void;
};

type Direction =
  | NonNullable<CollapseToggleProps["expandedDirection"]>
  | NonNullable<CollapseToggleProps["collapsedDirection"]>;

const rotationByDirection: Record<Direction, number> = {
  up: 180,
  down: 0,
  left: 90,
  right: 270,
};

export function CollapseToggle({
  expanded,
  expandedLabel,
  collapsedLabel,
  expandedDirection = "up",
  collapsedDirection = "down",
  className = "",
  onClick,
}: CollapseToggleProps): React.JSX.Element {
  const label = expanded ? expandedLabel : collapsedLabel;
  const direction = expanded ? expandedDirection : collapsedDirection;
  const classNames = ["icon-toggle", className].filter(Boolean).join(" ");

  return (
    <button
      className={classNames}
      type="button"
      aria-expanded={expanded}
      aria-label={label}
      title={label}
      onClick={onClick}
    >
      <svg
        aria-hidden="true"
        focusable="false"
        viewBox="0 0 24 24"
        style={{ transform: `rotate(${rotationByDirection[direction]}deg)` }}
      >
        <path d="M6 9l6 6 6-6" />
      </svg>
    </button>
  );
}
