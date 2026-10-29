import React, { useEffect, useMemo, useState } from "react";
import { ErrorState } from "../../components/layout/States";
import { governanceClient } from "../../lib/apiGovernance";
import type { GovernanceRecordUpdateInput } from "../../lib/apiTypes";
import { useLoad } from "../../lib/hooks";
import { GovernanceRecordDetail } from "./GovernanceRecordDetail";
import { GovernanceRecordFields } from "./GovernanceRecordFields";
import { GovernanceRecordList } from "./GovernanceRecordList";
import { GovernanceReviewContainer } from "./GovernanceReviewContainer";
import {
  blankGovernanceForm,
  fieldErrorsFromMessage,
  filtersFromLocation,
  governanceCreateInput,
  GovernanceFormValues,
  matchesListFilters,
  saveFiltersToLocation,
  validateGovernanceForm,
} from "./GovernancePage.helpers";
import { AppRoute } from "./types";

export type GovernancePageProps = {
  route: AppRoute;
  params: Record<string, string>;
  onNavigate: (_route: AppRoute, _params?: Record<string, string>) => void;
  canManage: boolean;
};

export function GovernancePage({
  route,
  params,
  onNavigate,
  canManage,
}: GovernancePageProps): React.JSX.Element {
  if (route === "/governance/review" || route === "/governance/review/:taskId") {
    return (
      <GovernanceReviewContainer
        params={params}
        onNavigate={(_reviewRoute, reviewParams) =>
          onNavigate("/governance/review/:taskId", reviewParams)
        }
      />
    );
  }
  return (
    <GovernanceRecordsWorkspace
      recordId={params.recordId}
      onNavigate={onNavigate}
      canManage={canManage}
    />
  );
}

type GovernanceRecordsWorkspaceProps = {
  recordId: string | undefined;
  onNavigate: GovernancePageProps["onNavigate"];
  canManage: boolean;
};

function GovernanceRecordsWorkspace({
  recordId,
  onNavigate,
  canManage,
}: GovernanceRecordsWorkspaceProps): React.JSX.Element {
  const [filters, setFilters] = useState(filtersFromLocation);
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm, setCreateForm] = useState<GovernanceFormValues>(blankGovernanceForm);
  const [createErrors, setCreateErrors] = useState<Record<string, string>>({});
  const [creating, setCreating] = useState(false);

  const remoteFilters = useMemo(
    () => ({
      recordType: filters.recordType || undefined,
      lifecycleStatus: filters.lifecycleStatus || undefined,
      ownerId: filters.ownerId.trim() || undefined,
      domainId: filters.domainId.trim() || undefined,
      externalIssueId: filters.externalIssueId.trim() || undefined,
    }),
    [
      filters.domainId,
      filters.externalIssueId,
      filters.lifecycleStatus,
      filters.ownerId,
      filters.recordType,
    ]
  );
  const records = useLoad(`governance-records-${JSON.stringify(remoteFilters)}`, () =>
    governanceClient.records(remoteFilters)
  );
  const detail = useLoad(
    `governance-record-${recordId ?? "none"}`,
    () => governanceClient.record(recordId ?? ""),
    { enabled: Boolean(recordId) }
  );

  useEffect(() => {
    saveFiltersToLocation(filters);
  }, [filters]);

  const visibleRecords = useMemo(
    () => (records.data ?? []).filter((record) => matchesListFilters(record, filters)),
    [filters, records.data]
  );

  const closeCreate = (): void => {
    if (creating) return;
    setCreateOpen(false);
    setCreateErrors({});
    setCreateForm(blankGovernanceForm());
  };

  const createRecord = async (): Promise<void> => {
    const errors = validateGovernanceForm(createForm);
    if (Object.keys(errors).length) {
      setCreateErrors(errors);
      return;
    }
    setCreating(true);
    setCreateErrors({});
    try {
      const created = await governanceClient.createRecord(governanceCreateInput(createForm));
      records.refresh();
      setCreateOpen(false);
      setCreateErrors({});
      setCreateForm(blankGovernanceForm());
      onNavigate("/governance/records/:recordId", { recordId: created.id });
    } catch (reason: unknown) {
      setCreateErrors(
        fieldErrorsFromMessage(
          reason instanceof Error ? reason.message : "Could not create governance record."
        )
      );
    } finally {
      setCreating(false);
    }
  };

  const refreshAfter = async (operation: () => Promise<unknown>): Promise<void> => {
    await operation();
    records.refresh();
    detail.refresh();
  };

  if (recordId) {
    return (
      <GovernanceRecordDetail
        record={detail.data}
        loading={detail.loading}
        error={detail.error}
        canManage={canManage}
        onBack={() => onNavigate("/governance")}
        onOpenRecord={(id) => onNavigate("/governance/records/:recordId", { recordId: id })}
        onOpenIdeaBlock={(id) =>
          onNavigate("/knowledge/ideablocks/:ideaBlockId", { ideaBlockId: id })
        }
        onOpenEntity={(id) => onNavigate("/knowledge/entities/:entityId", { entityId: id })}
        onExploreRelationships={() =>
          onNavigate("/knowledge/relationships/governance/:recordId", { recordId })
        }
        onOpenReviewQueue={() => onNavigate("/governance/review")}
        onSave={(input: GovernanceRecordUpdateInput) =>
          refreshAfter(() => governanceClient.updateRecord(recordId, input))
        }
        onLifecycleAction={(targetStatus, reason, note, attributes) => {
          const expectedRevisionId = detail.data?.currentRevision?.id;
          if (!expectedRevisionId)
            return Promise.reject(new Error("Refresh the record before transitioning it."));
          if (detail.data?.recordType === "risk" && targetStatus === "accepted") {
            return refreshAfter(() =>
              governanceClient.acceptRisk(recordId, { reason, expectedRevisionId })
            );
          }
          if (detail.data?.recordType === "risk" && targetStatus === "closed") {
            return refreshAfter(() =>
              governanceClient.closeRisk(recordId, { reason, expectedRevisionId })
            );
          }
          if (detail.data?.recordType === "waiver" && targetStatus === "revoked") {
            return refreshAfter(() =>
              governanceClient.revokeWaiver(recordId, { reason, expectedRevisionId })
            );
          }
          if (detail.data?.recordType === "technical_debt" && targetStatus === "resolved") {
            return refreshAfter(() =>
              governanceClient.resolveTechnicalDebt(recordId, {
                resolutionNote: note,
                reason,
                expectedRevisionId,
              })
            );
          }
          if (detail.data?.recordType === "architecture_backlog" && targetStatus === "completed") {
            return refreshAfter(() =>
              governanceClient.completeArchitectureBacklog(recordId, {
                completionNote: note,
                reason,
                expectedRevisionId,
              })
            );
          }
          return refreshAfter(() =>
            governanceClient.transitionRecord(recordId, {
              targetStatus,
              reason,
              attributes,
              expectedRevisionId,
            })
          );
        }}
        onRenewWaiver={(expiryDate, reason) => {
          const expectedRevisionId = detail.data?.currentRevision?.id;
          if (!expectedRevisionId)
            return Promise.reject(new Error("Refresh the record before renewing it."));
          return refreshAfter(() =>
            governanceClient.renewWaiver(recordId, { expiryDate, reason, expectedRevisionId })
          );
        }}
        onSupersede={(targetRecordId, reason) => {
          const expectedRevisionId = detail.data?.currentRevision?.id;
          if (!expectedRevisionId)
            return Promise.reject(new Error("Refresh the record before superseding it."));
          return refreshAfter(async () => {
            const target = await governanceClient.record(targetRecordId);
            const expectedTargetRevisionId = target.currentRevision?.id;
            if (!expectedTargetRevisionId)
              throw new Error("The successor record has no current revision; refresh and retry.");
            await governanceClient.supersedeRecord(recordId, {
              targetRecordId,
              reason,
              expectedRevisionId,
              expectedTargetRevisionId,
            });
          });
        }}
        onAddRelation={async (input) => {
          const expectedRevisionId = detail.data?.currentRevision?.id;
          if (!expectedRevisionId)
            return Promise.reject(new Error("Refresh the record before linking it."));
          return refreshAfter(async () => {
            const target = await governanceClient.record(input.targetRecordId);
            const expectedTargetRevisionId = target.currentRevision?.id;
            if (!expectedTargetRevisionId)
              throw new Error("The related record has no current revision; refresh and retry.");
            await governanceClient.addRelation(recordId, {
              ...input,
              expectedRevisionId,
              expectedTargetRevisionId,
            });
          });
        }}
        onLinkIdeaBlock={(ideaBlockId, role, reason) => {
          const expectedRevisionId = detail.data?.currentRevision?.id;
          if (!expectedRevisionId)
            return Promise.reject(new Error("Refresh the record before linking it."));
          return refreshAfter(() =>
            governanceClient.linkIdeaBlock(recordId, {
              ideaBlockId,
              role,
              reason,
              expectedRevisionId,
            })
          );
        }}
        onUnlinkIdeaBlock={(ideaBlockId, role, reason) => {
          const expectedRevisionId = detail.data?.currentRevision?.id;
          if (!expectedRevisionId)
            return Promise.reject(new Error("Refresh the record before unlinking it."));
          return refreshAfter(() =>
            governanceClient.unlinkIdeaBlock(
              recordId,
              ideaBlockId,
              role,
              reason,
              expectedRevisionId
            )
          );
        }}
        onLinkEntity={(entityId, role, reason) => {
          const expectedRevisionId = detail.data?.currentRevision?.id;
          if (!expectedRevisionId)
            return Promise.reject(new Error("Refresh the record before linking it."));
          return refreshAfter(() =>
            governanceClient.linkEntity(recordId, { entityId, role, reason, expectedRevisionId })
          );
        }}
        onUnlinkEntity={(entityId, role, reason) => {
          const expectedRevisionId = detail.data?.currentRevision?.id;
          if (!expectedRevisionId)
            return Promise.reject(new Error("Refresh the record before unlinking it."));
          return refreshAfter(() =>
            governanceClient.unlinkEntity(recordId, entityId, role, reason, expectedRevisionId)
          );
        }}
        onCreateBacklog={async (input) => {
          const expectedOriginRevisionId = detail.data?.currentRevision?.id;
          if (!expectedOriginRevisionId)
            throw new Error(
              "Refresh the origin record before creating an Architecture Backlog item."
            );
          const created = await governanceClient.createArchitectureBacklog(recordId, {
            ...input,
            expectedOriginRevisionId,
          });
          records.refresh();
          onNavigate("/governance/records/:recordId", { recordId: created.id });
        }}
      />
    );
  }

  return (
    <section className="page-grid">
      <GovernanceRecordList
        records={records.data === null ? null : visibleRecords}
        loading={records.loading}
        error={records.error}
        filters={filters}
        onFiltersChange={setFilters}
        onOpenRecord={(id) => onNavigate("/governance/records/:recordId", { recordId: id })}
        onCreateRecord={() => setCreateOpen(true)}
        canCreate={canManage}
      />

      <article className="card">
        <p className="eyebrow">Architecture views</p>
        <h2>Inspect governed architecture</h2>
        <p className="subtle">
          Generate the current architecture wiki or review health overlays and C4 structure without
          leaving the governance workflow.
        </p>
        <div className="actions-row">
          <button type="button" onClick={() => onNavigate("/architecture/wiki")}>
            Generate architecture wiki
          </button>
          <button type="button" onClick={() => onNavigate("/views/heatmap")}>
            Open health view
          </button>
          <button type="button" onClick={() => onNavigate("/views/c4")}>
            Open C4 explorer
          </button>
        </div>
      </article>

      {createOpen ? (
        <div className="modal-backdrop">
          <form
            className="modal-dialog stacked-form"
            role="dialog"
            aria-modal="true"
            aria-labelledby="new-governance-record-title"
            onSubmit={(event) => {
              event.preventDefault();
              void createRecord();
            }}
          >
            <div className="modal-header">
              <div>
                <p className="eyebrow">Architecture Governance</p>
                <h2 id="new-governance-record-title">New governance record</h2>
              </div>
              <button type="button" onClick={closeCreate} disabled={creating}>
                Close
              </button>
            </div>
            <p className="subtle">
              Record type, business key, and domain establish the governed identity. Later changes
              become revisions.
            </p>
            <GovernanceRecordFields
              values={createForm}
              errors={createErrors}
              disabled={creating}
              includeIdentity
              onChange={setCreateForm}
            />
            <ErrorState error={createErrors.form ?? null} />
            <div className="actions-row">
              <button type="submit" disabled={creating}>
                Create record
              </button>
              <button type="button" onClick={closeCreate} disabled={creating}>
                Cancel
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </section>
  );
}
