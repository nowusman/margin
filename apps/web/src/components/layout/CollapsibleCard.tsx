import React, { useState } from "react";
import { CollapseToggle } from "./CollapseToggle";

type CollapsibleCardProps = {
  title: string;
  children: React.ReactNode;
  className?: string;
  defaultOpen?: boolean;
  tabIndex?: number;
};

export const CollapsibleCard = React.forwardRef<HTMLElement, CollapsibleCardProps>(
  ({ title, children, className = "", defaultOpen = true, tabIndex }, ref): React.JSX.Element => {
    const [open, setOpen] = useState(defaultOpen);
    const classNames = ["card", "collapsible-card", className].filter(Boolean).join(" ");

    return (
      <article ref={ref} className={classNames} tabIndex={tabIndex}>
        <div className="collapsible-card-header">
          <h2>{title}</h2>
          <CollapseToggle
            expanded={open}
            expandedLabel="Collapse widget"
            collapsedLabel="Expand widget"
            onClick={() => setOpen((value) => !value)}
          />
        </div>
        {open ? <div className="collapsible-card-body">{children}</div> : null}
      </article>
    );
  }
);

CollapsibleCard.displayName = "CollapsibleCard";
