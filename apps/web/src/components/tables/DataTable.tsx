import React from "react";

type Column<T> = {
  header: string;
  render: (_row: T) => React.ReactNode;
  sortValue?: (_row: T) => string | number;
};

type DataTableProps<T> = {
  rows: T[];
  columns: Array<Column<T>>;
  emptyLabel: string;
  getRowKey?: (_row: T, _index: number) => string;
  caption?: string;
  density?: "compact" | "comfortable";
};

export function DataTable<T>({
  rows,
  columns,
  emptyLabel,
  getRowKey,
  caption,
  density = "comfortable",
}: DataTableProps<T>): React.JSX.Element {
  const [sortHeader, setSortHeader] = React.useState<string | null>(null);
  const [sortDirection, setSortDirection] = React.useState<"asc" | "desc">("asc");

  const sortedRows = React.useMemo(() => {
    const column = columns.find((item) => item.header === sortHeader && item.sortValue);
    if (!column?.sortValue) return rows;
    return [...rows].sort((left, right) => {
      const leftValue = column.sortValue?.(left) ?? "";
      const rightValue = column.sortValue?.(right) ?? "";
      const comparison =
        typeof leftValue === "number" && typeof rightValue === "number"
          ? leftValue - rightValue
          : String(leftValue).localeCompare(String(rightValue));
      return sortDirection === "asc" ? comparison : -comparison;
    });
  }, [columns, rows, sortDirection, sortHeader]);

  const toggleSort = (column: Column<T>): void => {
    if (!column.sortValue) return;
    if (sortHeader === column.header) {
      setSortDirection((value) => (value === "asc" ? "desc" : "asc"));
      return;
    }
    setSortHeader(column.header);
    setSortDirection("asc");
  };

  if (rows.length === 0) return <p className="subtle">{emptyLabel}</p>;

  return (
    <div className={`table-scroll table-density-${density}`}>
      <table className="data-table">
        {caption ? <caption>{caption}</caption> : null}
        <thead>
          <tr>
            {columns.map((column) => {
              const active = sortHeader === column.header;
              const label = active
                ? `${column.header} ${sortDirection === "asc" ? "ascending" : "descending"}`
                : column.header;
              return (
                <th
                  key={column.header}
                  aria-sort={
                    active ? (sortDirection === "asc" ? "ascending" : "descending") : undefined
                  }
                >
                  {column.sortValue ? (
                    <button
                      type="button"
                      className="table-sort-button"
                      onClick={() => toggleSort(column)}
                      aria-label={`Sort by ${label}`}
                    >
                      {column.header}
                      {active ? (
                        <span aria-hidden="true">{sortDirection === "asc" ? " asc" : " desc"}</span>
                      ) : null}
                    </button>
                  ) : (
                    column.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sortedRows.map((row, index) => (
            <tr key={getRowKey ? getRowKey(row, index) : index}>
              {columns.map((column) => (
                <td key={column.header} data-label={column.header}>
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
