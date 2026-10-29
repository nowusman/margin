import React from "react";

type BreadcrumbsProps = {
  items: string[];
};

export function Breadcrumbs({ items }: BreadcrumbsProps): React.JSX.Element {
  return (
    <nav aria-label="Breadcrumb" className="breadcrumbs">
      {items.map((item, index) => (
        <span key={`${item}-${index}`}>
          {item}
          {index < items.length - 1 ? <span className="separator">/</span> : null}
        </span>
      ))}
    </nav>
  );
}
