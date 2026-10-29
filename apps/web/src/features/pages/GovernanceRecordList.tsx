import React from "react";
import { StatusBadge } from "../../components/badges/Badges";
import { EmptyState, ErrorState, LoadingState } from "../../components/layout/States";
import { DataTable } from "../../components/tables";
import type { GovernanceRecord, GovernanceRecordType } from "../../lib/apiTypes";
import { shortId } from "../../lib/display";
import {
  GovernanceListFilters,
  GOVERNANCE_RECORD_TYPES,
  lifecycleOptions,
  typeLabel,
} from "./GovernancePage.helpers";

type GovernanceRecordListProps = {
  records: GovernanceRecord[] | null;
  loading: boolean;
  error: string | null;
  filters: GovernanceListFilters;
  onFiltersChange: (_filters: GovernanceListFilters) => void;
  onOpenRecord: (_recordId: string) => void;
  onCreateRecord: () => void;
  canCreate: boolean;
};

const allLifecycleStates = Array.from(new Set(Object.values(lifecycleOptions).flat()));

const dateSummary = (record: GovernanceRecord): string =>
  [
    record.reviewDate ? `Review ${record.reviewDate}` : "",
    record.expiryDate ? `Expiry ${record.expiryDate}` : "",
    record.targetDate ? `Target ${record.targetDate}` : "",
  ]
    .filter(Boolean)
    .join(" · ") || "Not scheduled";

export function GovernanceRecordList({
  records,
  loading,
  error,
  filters,
  onFiltersChange,
  onOpenRecord,
  onCreateRecord,
  canCreate,
}: GovernanceRecordListProps): React.JSX.Element {
  const update = <K extends keyof GovernanceListFilters>(
    key: K,
    value: GovernanceListFilters[K]
  ): void => {
    onFiltersChange({ ...filters, [key]: value });
  };
  const resetFilters = (): void => {
    onFiltersChange({
      recordType: "",
      lifecycleStatus: "",
      ownerId: "",
      domainId: "",
      externalIssueId: "",
      reviewBefore: "",
      expiryBefore: "",
      targetBefore: "",
      focusedView: "all",
    });
  };

  return (
    <article className="card">
      <div className="listview-header">
        <div>
          <p className="eyebrow">Architecture Governance</p>
          <h2>Governance records</h2>
          <p className="subtle">
            Decisions, risks, waivers, technical debt, and Architecture Backlog work in one governed
            flow. ADR is the decision record type; open one to inspect its immutable revision
            history.
          </p>
        </div>
        <div className="listview-actions">
          <button type="button" onClick={resetFilters}>
            Clear filters
          </button>
          {canCreate ? (
            <button type="button" onClick={onCreateRecord}>
              New record
            </button>
          ) : null}
        </div>
      </div>

      <div className="filter-grid" aria-label="Governance record filters">
        <label className="field">
          <span>Focused view</span>
          <select
            value={filters.focusedView}
            onChange={(event) =>
              update("focusedView", event.target.value as GovernanceListFilters["focusedView"])
            }
          >
            <option value="all">All records</option>
            <option value="decisions">Decision history</option>
            <option value="open_risks">Open or accepted risks</option>
            <option value="expiring_waivers">Expiring waivers</option>
            <option value="unresolved_debt">Unresolved technical debt</option>
            <option value="backlog">Architecture Backlog</option>
          </select>
        </label>
        <label className="field">
          <span>Record type</span>
          <select
            value={filters.recordType}
            onChange={(event) =>
              update("recordType", event.target.value as "" | GovernanceRecordType)
            }
          >
            <option value="">All record types</option>
            {GOVERNANCE_RECORD_TYPES.map((recordType) => (
              <option key={recordType} value={recordType}>
                {typeLabel(recordType)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Lifecycle state</span>
          <select
            value={filters.lifecycleStatus}
            onChange={(event) => update("lifecycleStatus", event.target.value)}
          >
            <option value="">All lifecycle states</option>
            {allLifecycleStates.map((state) => (
              <option key={state} value={state}>
                {state}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Owner ID</span>
          <input
            value={filters.ownerId}
            onChange={(event) => update("ownerId", event.target.value)}
            placeholder="Owner ID"
          />
        </label>
        <label className="field">
          <span>Domain ID</span>
          <input
            value={filters.domainId}
            onChange={(event) => update("domainId", event.target.value)}
            placeholder="Domain ID"
          />
        </label>
        <label className="field">
          <span>External reference</span>
          <input
            value={filters.externalIssueId}
            onChange={(event) => update("externalIssueId", event.target.value)}
            placeholder="Issue ID"
          />
        </label>
        <label className="field">
          <span>Review due on or before</span>
          <input
            type="date"
            value={filters.reviewBefore}
            onChange={(event) => update("reviewBefore", event.target.value)}
          />
        </label>
        <label className="field">
          <span>Expiry on or before</span>
          <input
            type="date"
            value={filters.expiryBefore}
            onChange={(event) => update("expiryBefore", event.target.value)}
          />
        </label>
        <label className="field">
          <span>Target date on or before</span>
          <input
            type="date"
            value={filters.targetBefore}
            onChange={(event) => update("targetBefore", event.target.value)}
          />
        </label>
      </div>

      {loading ? <LoadingState label="Loading governance records..." /> : null}
      <ErrorState error={error} />
      {records && records.length > 0 ? (
        <DataTable
          rows={records}
          emptyLabel="No governance records match these filters."
          getRowKey={(record) => record.id}
          caption="Governance records"
          columns={[
            {
              header: "Record",
              render: (record) => (
                <button
                  type="button"
                  className="table-link"
                  onClick={() => onOpenRecord(record.id)}
                >
                  {record.title || record.businessKey || shortId(record.id)}
                </button>
              ),
              sortValue: (record) => record.title,
            },
            {
              header: "Type",
              render: (record) => typeLabel(record.recordType),
              sortValue: (record) => record.recordType,
            },
            {
              header: "Lifecycle",
              render: (record) => <StatusBadge label={record.lifecycleStatus} />,
              sortValue: (record) => record.lifecycleStatus,
            },
            {
              header: "Domain",
              render: (record) => record.domainId || "Unassigned",
              sortValue: (record) => record.domainId,
            },
            {
              header: "Owner",
              render: (record) => record.ownerId || "Unassigned",
              sortValue: (record) => record.ownerId || "",
            },
            { header: "Dates", render: dateSummary, sortValue: dateSummary },
            {
              header: "External reference",
              render: (record) => record.externalIssueId || "None",
              sortValue: (record) => record.externalIssueId || "",
            },
          ]}
        />
      ) : !loading && !error ? (
        <EmptyState
          label="No governance records match these filters."
          action={
            canCreate ? (
              <button type="button" onClick={onCreateRecord}>
                Create a record
              </button>
            ) : undefined
          }
        />
      ) : null}
    </article>
  );
}
