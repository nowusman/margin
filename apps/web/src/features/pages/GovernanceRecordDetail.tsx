import React, { useEffect, useState } from "react";
import { StatusBadge } from "../../components/badges/Badges";
import { EvidenceCard } from "../../components/evidence/EvidenceCard";
import { TextAreaField, TextField } from "../../components/forms";
import { EmptyState, ErrorState, LoadingState } from "../../components/layout/States";
import { DataTable } from "../../components/tables";
import type { GovernanceRecord } from "../../lib/apiTypes";
import { confirmGovernedAction, shortId } from "../../lib/display";
import { GovernanceRecordFields } from "./GovernanceRecordFields";
import {
  governanceFormFromRecord,
  governanceUpdateInput,
  GovernanceFormValues,
  fieldErrorsFromMessage,
  transitionTargets,
  typeLabel,
  validateGovernanceForm,
} from "./GovernancePage.helpers";

type GovernanceRecordDetailProps = {
  record: GovernanceRecord | null;
  loading: boolean;
  error: string | null;
  onBack: () => void;
  onOpenRecord: (_recordId: string) => void;
  onOpenIdeaBlock: (_ideaBlockId: string) => void;
  onOpenEntity: (_entityId: string) => void;
  onExploreRelationships: () => void;
  onOpenReviewQueue: () => void;
  onSave: (_input: ReturnType<typeof governanceUpdateInput>) => Promise<void>;
  onLifecycleAction: (
    _targetStatus: string,
    _reason: string,
    _note: string,
    _attributes: Record<string, unknown>
  ) => Promise<void>;
  onRenewWaiver: (_expiryDate: string, _reason: string) => Promise<void>;
  onSupersede: (_targetRecordId: string, _reason: string) => Promise<void>;
  onAddRelation: (_input: {
    targetRecordId: string;
    relationType: string;
    reason: string;
    status: string;
    validFrom: string | null;
    validTo: string | null;
  }) => Promise<void>;
  onLinkIdeaBlock: (_ideaBlockId: string, _role: string, _reason: string) => Promise<void>;
  onUnlinkIdeaBlock: (_ideaBlockId: string, _role: string, _reason: string) => Promise<void>;
  onLinkEntity: (_entityId: string, _role: string, _reason: string) => Promise<void>;
  onUnlinkEntity: (_entityId: string, _role: string, _reason: string) => Promise<void>;
  onCreateBacklog: (_input: {
    businessKey: string;
    title: string;
    pendingCorrection: string;
    reason: string;
    priority: string;
    targetDate: string;
  }) => Promise<void>;
  canManage: boolean;
};

const dateLabel = (value: string | null): string => value || "Not set";
const timestamp = (value: string | null): string =>
  value ? new Date(value).toLocaleString() : "Not available";

export function GovernanceRecordDetail({
  record,
  loading,
  error,
  onBack,
  onOpenRecord,
  onOpenIdeaBlock,
  onOpenEntity,
  onExploreRelationships,
  onOpenReviewQueue,
  onSave,
  onLifecycleAction,
  onRenewWaiver,
  onSupersede,
  onAddRelation,
  onLinkIdeaBlock,
  onUnlinkIdeaBlock,
  onLinkEntity,
  onUnlinkEntity,
  onCreateBacklog,
  canManage,
}: GovernanceRecordDetailProps): React.JSX.Element {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<GovernanceFormValues | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [targetStatus, setTargetStatus] = useState("");
  const [transitionReason, setTransitionReason] = useState("");
  const [transitionNote, setTransitionNote] = useState("");
  const [renewalExpiryDate, setRenewalExpiryDate] = useState("");
  const [renewalReason, setRenewalReason] = useState("");
  const [relatedRecordId, setRelatedRecordId] = useState("");
  const [relationType, setRelationType] = useState("mitigates");
  const [relationStatus, setRelationStatus] = useState("candidate");
  const [relationValidFrom, setRelationValidFrom] = useState("");
  const [relationValidTo, setRelationValidTo] = useState("");
  const [relationReason, setRelationReason] = useState("");
  const [ideaBlockId, setIdeaBlockId] = useState("");
  const [ideaBlockRole, setIdeaBlockRole] = useState("supporting");
  const [ideaBlockReason, setIdeaBlockReason] = useState("");
  const [entityId, setEntityId] = useState("");
  const [entityRole, setEntityRole] = useState("governed");
  const [entityLinkReason, setEntityLinkReason] = useState("");
  const [backlogKey, setBacklogKey] = useState("");
  const [backlogTitle, setBacklogTitle] = useState("");
  const [pendingCorrection, setPendingCorrection] = useState("");
  const [backlogReason, setBacklogReason] = useState("");
  const [backlogPriority, setBacklogPriority] = useState("");
  const [backlogTargetDate, setBacklogTargetDate] = useState("");

  useEffect(() => {
    setEditing(false);
    setForm(record ? governanceFormFromRecord(record) : null);
    setFieldErrors({});
    setMessage(null);
    setTargetStatus("");
    setTransitionReason("");
    setTransitionNote("");
    setRelationStatus("candidate");
    setRelationValidFrom("");
    setRelationValidTo("");
    setRenewalExpiryDate("");
    setRenewalReason("");
    setIdeaBlockReason("");
    setEntityLinkReason("");
  }, [record?.id, record?.currentRevision?.id]);

  const run = async (label: string, action: () => Promise<void>): Promise<boolean> => {
    setBusy(true);
    setMessage(null);
    try {
      await action();
      setMessage(`${label} succeeded.`);
      return true;
    } catch (reason: unknown) {
      const detail = reason instanceof Error ? reason.message : "Request failed";
      setMessage(`${label} failed: ${detail}`);
      return false;
    } finally {
      setBusy(false);
    }
  };

  if (loading) return <LoadingState label="Loading governance record..." />;
  if (error) return <ErrorState error={error} />;
  if (!record)
    return (
      <EmptyState
        label="This governance record is no longer available."
        action={
          <button type="button" onClick={onBack}>
            Back to records
          </button>
        }
      />
    );

  const transitions = transitionTargets(record);
  const currentRevisionId = record.currentRevision?.id;
  const submitEdit = async (): Promise<void> => {
    if (!form || !currentRevisionId) {
      setFieldErrors({ form: "This record has no current revision. Refresh before editing." });
      return;
    }
    const errors = validateGovernanceForm(form);
    if (Object.keys(errors).length) {
      setFieldErrors(errors);
      return;
    }
    const reason =
      window.prompt("Explain this change for the immutable revision history.")?.trim() || "";
    if (!reason) {
      setFieldErrors({ form: "A change reason is required." });
      return;
    }
    const succeeded = await run("Update governance record", async () => {
      try {
        await onSave(governanceUpdateInput(form, currentRevisionId, reason));
      } catch (reason: unknown) {
        const detail = reason instanceof Error ? reason.message : "Request failed";
        setFieldErrors(fieldErrorsFromMessage(detail));
        throw reason;
      }
    });
    if (succeeded) setEditing(false);
  };

  const submitTransition = async (): Promise<void> => {
    if (!targetStatus || !transitionReason.trim()) {
      setMessage("Choose a lifecycle state and enter a transition reason.");
      return;
    }
    const attributes = { ...record.attributes };
    if (targetStatus === "resolved") attributes.resolution_note = transitionNote.trim();
    if (targetStatus === "completed") attributes.completion_note = transitionNote.trim();
    if ((targetStatus === "resolved" || targetStatus === "completed") && !transitionNote.trim()) {
      setMessage(
        `A ${targetStatus === "resolved" ? "resolution" : "completion"} note is required.`
      );
      return;
    }
    if (!confirmGovernedAction(`Transition “${record.title}” to ${targetStatus}?`)) return;
    await run("Transition governance record", async () => {
      await onLifecycleAction(
        targetStatus,
        transitionReason.trim(),
        transitionNote.trim(),
        attributes
      );
      setTargetStatus("");
      setTransitionReason("");
      setTransitionNote("");
    });
  };

  const linkActionsDisabled = busy;
  const canCreateBacklog =
    record.recordType !== "architecture_backlog" &&
    {
      adr: ["proposed", "accepted"],
      waiver: ["approved"],
      risk: ["accepted"],
      technical_debt: ["identified", "accepted", "planned", "in_progress"],
      architecture_backlog: [],
    }[record.recordType].includes(record.lifecycleStatus);

  return (
    <section className="page-grid">
      <article className="card">
        <div className="listview-header">
          <div>
            <p className="eyebrow">{typeLabel(record.recordType)}</p>
            <h2>{record.title}</h2>
            <p className="subtle">
              {record.businessKey} · {record.domainId}
            </p>
          </div>
          <div className="listview-actions">
            <button type="button" onClick={onBack}>
              Back to records
            </button>
            <button type="button" onClick={onOpenReviewQueue}>
              Review queue
            </button>
            {canManage ? (
              <button
                type="button"
                disabled={!currentRevisionId || busy}
                onClick={() => setEditing((value) => !value)}
              >
                {editing ? "Cancel edit" : "Edit record"}
              </button>
            ) : null}
          </div>
        </div>

        <div className="badge-row">
          <StatusBadge label={record.lifecycleStatus} />
          {record.priority ? <StatusBadge label={record.priority} /> : null}
          {record.severity !== null ? (
            <span className="badge">Severity {record.severity}</span>
          ) : null}
        </div>
        <dl>
          <dt>Owner</dt>
          <dd>{record.ownerId || "Unassigned"}</dd>
          <dt>Steward</dt>
          <dd>{record.stewardId || "Unassigned"}</dd>
          <dt>Effective date</dt>
          <dd>{dateLabel(record.effectiveDate)}</dd>
          <dt>Review date</dt>
          <dd>{dateLabel(record.reviewDate)}</dd>
          <dt>Expiry date</dt>
          <dd>{dateLabel(record.expiryDate)}</dd>
          <dt>Target date</dt>
          <dd>{dateLabel(record.targetDate)}</dd>
          <dt>External reference</dt>
          <dd>
            {record.externalIssueUrl ? (
              <a href={record.externalIssueUrl} target="_blank" rel="noreferrer">
                {record.externalIssueId || record.externalIssueUrl}
              </a>
            ) : (
              record.externalIssueId || "None"
            )}
          </dd>
        </dl>
        <details className="technical-details">
          <summary>Record metadata</summary>
          <p className="technical-id">ID: {record.id}</p>
          <p className="technical-id">Schema: {record.schemaVersion}</p>
        </details>

        {editing && form ? (
          <section className="stacked-form" aria-labelledby="edit-governance-record-title">
            <h3 id="edit-governance-record-title">Edit {typeLabel(record.recordType)}</h3>
            <p className="subtle">
              Changes create a new immutable revision. The business key, type, and domain remain
              stable.
            </p>
            <GovernanceRecordFields
              values={form}
              errors={fieldErrors}
              disabled={busy}
              onChange={setForm}
            />
            <ErrorState error={fieldErrors.form ?? null} />
            <div className="actions-row">
              <button type="button" disabled={busy} onClick={() => void submitEdit()}>
                Save new revision
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setEditing(false);
                  setFieldErrors({});
                  setForm(governanceFormFromRecord(record));
                }}
              >
                Cancel
              </button>
            </div>
          </section>
        ) : null}
      </article>

      <article className="card">
        <h3>Lifecycle transition</h3>
        {!canManage ? (
          <EmptyState label="Your access to this governance record is read only." />
        ) : transitions.length ? (
          <>
            <label className="field">
              <span>Target lifecycle state</span>
              <select
                disabled={busy}
                value={targetStatus}
                onChange={(event) => setTargetStatus(event.target.value)}
              >
                <option value="">Choose a state</option>
                {transitions.map((state) => (
                  <option key={state} value={state}>
                    {state}
                  </option>
                ))}
              </select>
            </label>
            {targetStatus === "resolved" || targetStatus === "completed" ? (
              <TextAreaField
                label={targetStatus === "resolved" ? "Resolution note" : "Completion note"}
                value={transitionNote}
                disabled={busy}
                onChange={setTransitionNote}
                rows={3}
              />
            ) : null}
            <TextAreaField
              label="Transition reason"
              value={transitionReason}
              disabled={busy}
              onChange={setTransitionReason}
              rows={3}
            />
            <button
              type="button"
              disabled={busy || !targetStatus || !transitionReason.trim()}
              onClick={() => void submitTransition()}
            >
              Apply transition
            </button>
          </>
        ) : (
          <EmptyState label="No lifecycle transitions are available from this state." />
        )}
      </article>

      {canManage && record.recordType === "waiver" && record.lifecycleStatus === "approved" ? (
        <article className="card">
          <h3>Renew waiver</h3>
          <p className="subtle">
            A renewal extends the current expiry and records a new immutable revision.
          </p>
          <label className="field">
            <span>New expiry date</span>
            <input
              type="date"
              value={renewalExpiryDate}
              disabled={busy}
              onChange={(event) => setRenewalExpiryDate(event.target.value)}
            />
          </label>
          <TextAreaField
            label="Renewal reason"
            value={renewalReason}
            disabled={busy}
            onChange={setRenewalReason}
            rows={3}
          />
          <button
            type="button"
            disabled={busy || !renewalExpiryDate || !renewalReason.trim()}
            onClick={() =>
              void run("Renew waiver", async () => {
                await onRenewWaiver(renewalExpiryDate, renewalReason.trim());
                setRenewalExpiryDate("");
                setRenewalReason("");
              })
            }
          >
            Renew waiver
          </button>
        </article>
      ) : null}

      <article className="card">
        <h3>Revision history</h3>
        <DataTable
          rows={record.history}
          emptyLabel="No revision history was returned."
          getRowKey={(revision) => revision.id}
          density="compact"
          columns={[
            {
              header: "Revision",
              render: (revision) => `#${revision.revisionNumber}`,
              sortValue: (revision) => revision.revisionNumber,
            },
            {
              header: "Reason",
              render: (revision) => revision.transitionReason || "Not recorded",
              sortValue: (revision) => revision.transitionReason,
            },
            {
              header: "Actor",
              render: (revision) => revision.actor || "Unknown",
              sortValue: (revision) => revision.actor,
            },
            {
              header: "Process",
              render: (revision) => revision.process || "Unknown",
              sortValue: (revision) => revision.process,
            },
            {
              header: "When",
              render: (revision) => timestamp(revision.createdAt),
              sortValue: (revision) => revision.createdAt || "",
            },
            {
              header: "Snapshot",
              render: (revision) => (
                <details>
                  <summary>View</summary>
                  <pre className="code-block">{JSON.stringify(revision.snapshot, null, 2)}</pre>
                </details>
              ),
            },
          ]}
        />
      </article>

      <article className="card">
        <h3>Audit metadata</h3>
        <DataTable
          rows={record.audit}
          emptyLabel="No audit events were returned."
          getRowKey={(event) => event.id}
          density="compact"
          columns={[
            {
              header: "Action",
              render: (event) => event.action,
              sortValue: (event) => event.action,
            },
            { header: "Actor", render: (event) => event.actor, sortValue: (event) => event.actor },
            {
              header: "Target",
              render: (event) => event.targetType,
              sortValue: (event) => event.targetType,
            },
            {
              header: "Correlation",
              render: (event) => event.correlationId || "None",
              sortValue: (event) => event.correlationId || "",
            },
            {
              header: "When",
              render: (event) => timestamp(event.createdAt),
              sortValue: (event) => event.createdAt || "",
            },
          ]}
        />
      </article>

      <article className="card">
        <h3>Evidence</h3>
        {record.evidence.length ? (
          <div className="page-grid">
            {record.evidence.map((evidence) => (
              <EvidenceCard
                key={evidence.id}
                evidence={{
                  sourceTitle: evidence.sourceElementId
                    ? `Source element ${shortId(evidence.sourceElementId)}`
                    : "Governance revision evidence",
                  elementLabel: evidence.sourceElementId || "Governance record revision",
                  quote: evidence.sourceQuote,
                  ideaBlockId:
                    evidence.ideaBlockId || evidence.governanceRecordRevisionId || evidence.id,
                  verificationStatus: evidence.status,
                  confidence: evidence.polarity,
                  permissionsState: "Authorized evidence",
                }}
                onOpenIdeaBlock={
                  evidence.ideaBlockId
                    ? () => onOpenIdeaBlock(evidence.ideaBlockId as string)
                    : undefined
                }
              />
            ))}
          </div>
        ) : (
          <EmptyState label="No supporting or refuting evidence is linked to this record." />
        )}
      </article>

      <article className="card">
        <h3>Linked Idea Blocks</h3>
        <DataTable
          rows={record.ideaBlocks}
          emptyLabel="No Idea Blocks are linked."
          getRowKey={(link) => `${link.id}-${link.role}`}
          density="compact"
          columns={[
            {
              header: "Idea Block",
              render: (link) => (
                <button
                  type="button"
                  className="table-link"
                  onClick={() => onOpenIdeaBlock(link.id)}
                >
                  {shortId(link.id)}
                </button>
              ),
              sortValue: (link) => link.id,
            },
            { header: "Role", render: (link) => link.role, sortValue: (link) => link.role },
            {
              header: "Order",
              render: (link) => String(link.displayOrder),
              sortValue: (link) => link.displayOrder,
            },
            {
              header: "Action",
              render: (link) =>
                canManage ? (
                  <button
                    type="button"
                    disabled={linkActionsDisabled}
                    onClick={() => {
                      if (!ideaBlockReason.trim()) {
                        setMessage("Enter a link reason before removing an Idea Block.");
                        return;
                      }
                      if (confirmGovernedAction("Remove this Idea Block link?"))
                        void run("Remove Idea Block link", () =>
                          onUnlinkIdeaBlock(link.id, link.role, ideaBlockReason.trim())
                        );
                    }}
                  >
                    Remove
                  </button>
                ) : (
                  "Read only"
                ),
            },
          ]}
        />
        {canManage ? (
          <div className="filter-grid">
            <TextField
              label="Idea Block ID"
              value={ideaBlockId}
              disabled={busy}
              onChange={setIdeaBlockId}
            />
            <TextField
              label="Link role"
              value={ideaBlockRole}
              disabled={busy}
              onChange={setIdeaBlockRole}
            />
            <TextField
              label="Link reason"
              value={ideaBlockReason}
              disabled={busy}
              onChange={setIdeaBlockReason}
            />
            <button
              type="button"
              disabled={
                busy || !ideaBlockId.trim() || !ideaBlockRole.trim() || !ideaBlockReason.trim()
              }
              onClick={() =>
                void run("Link Idea Block", async () => {
                  await onLinkIdeaBlock(
                    ideaBlockId.trim(),
                    ideaBlockRole.trim(),
                    ideaBlockReason.trim()
                  );
                  setIdeaBlockId("");
                  setIdeaBlockReason("");
                })
              }
            >
              Link Idea Block
            </button>
          </div>
        ) : null}
      </article>

      <article className="card">
        <div className="listview-header">
          <h3>Governed entities</h3>
          <button type="button" onClick={onExploreRelationships}>
            Explore relationships
          </button>
        </div>
        <DataTable
          rows={record.entities}
          emptyLabel="No entities are linked."
          getRowKey={(link) => `${link.id}-${link.role}`}
          density="compact"
          columns={[
            {
              header: "Entity",
              render: (link) => (
                <button type="button" className="table-link" onClick={() => onOpenEntity(link.id)}>
                  {shortId(link.id)}
                </button>
              ),
              sortValue: (link) => link.id,
            },
            { header: "Role", render: (link) => link.role, sortValue: (link) => link.role },
            {
              header: "Action",
              render: (link) =>
                canManage ? (
                  <button
                    type="button"
                    disabled={linkActionsDisabled}
                    onClick={() => {
                      if (!entityLinkReason.trim()) {
                        setMessage("Enter a link reason before removing an entity.");
                        return;
                      }
                      if (confirmGovernedAction("Remove this entity link?"))
                        void run("Remove entity link", () =>
                          onUnlinkEntity(link.id, link.role, entityLinkReason.trim())
                        );
                    }}
                  >
                    Remove
                  </button>
                ) : (
                  "Read only"
                ),
            },
          ]}
        />
        {canManage ? (
          <div className="filter-grid">
            <TextField label="Entity ID" value={entityId} disabled={busy} onChange={setEntityId} />
            <TextField
              label="Link role"
              value={entityRole}
              disabled={busy}
              onChange={setEntityRole}
            />
            <TextField
              label="Link reason"
              value={entityLinkReason}
              disabled={busy}
              onChange={setEntityLinkReason}
            />
            <button
              type="button"
              disabled={busy || !entityId.trim() || !entityRole.trim() || !entityLinkReason.trim()}
              onClick={() =>
                void run("Link entity", async () => {
                  await onLinkEntity(entityId.trim(), entityRole.trim(), entityLinkReason.trim());
                  setEntityId("");
                  setEntityLinkReason("");
                })
              }
            >
              Link entity
            </button>
          </div>
        ) : null}
      </article>

      <article className="card">
        <h3>Related governance records</h3>
        <DataTable
          rows={record.relations}
          emptyLabel="No governance record relationships are linked."
          getRowKey={(relation) => relation.id}
          density="compact"
          columns={[
            {
              header: "Relation",
              render: (relation) => relation.relationType,
              sortValue: (relation) => relation.relationType,
            },
            {
              header: "Status",
              render: (relation) => <StatusBadge label={relation.status} />,
              sortValue: (relation) => relation.status,
            },
            {
              header: "Validity",
              render: (relation) =>
                `${dateLabel(relation.validFrom)} – ${dateLabel(relation.validTo)}`,
              sortValue: (relation) => relation.validFrom || "",
            },
            {
              header: "Related record",
              render: (relation) => {
                const id =
                  relation.sourceRecordId === record.id
                    ? relation.targetRecordId
                    : relation.sourceRecordId;
                return (
                  <button type="button" className="table-link" onClick={() => onOpenRecord(id)}>
                    {shortId(id)}
                  </button>
                );
              },
              sortValue: (relation) =>
                relation.sourceRecordId === record.id
                  ? relation.targetRecordId
                  : relation.sourceRecordId,
            },
          ]}
        />
        {canManage ? (
          <div className="filter-grid">
            <TextField
              label="Related record ID"
              value={relatedRecordId}
              disabled={busy}
              onChange={setRelatedRecordId}
            />
            <label className="field">
              <span>Relation type</span>
              <select
                disabled={busy}
                value={relationType}
                onChange={(event) => setRelationType(event.target.value)}
              >
                {["mitigates", "waives", "incurs", "resolves", "corrects"].map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Review status</span>
              <select
                disabled={busy}
                value={relationStatus}
                onChange={(event) => setRelationStatus(event.target.value)}
              >
                {["candidate", "verified", "rejected", "stale"].map((status) => (
                  <option key={status} value={status}>
                    {status}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Valid from</span>
              <input
                type="date"
                disabled={busy}
                value={relationValidFrom}
                onChange={(event) => setRelationValidFrom(event.target.value)}
              />
            </label>
            <label className="field">
              <span>Valid to</span>
              <input
                type="date"
                disabled={busy}
                value={relationValidTo}
                onChange={(event) => setRelationValidTo(event.target.value)}
              />
            </label>
            <TextField
              label="Link reason"
              value={relationReason}
              disabled={busy}
              onChange={setRelationReason}
            />
            <button
              type="button"
              disabled={busy || !relatedRecordId.trim() || !relationReason.trim()}
              onClick={() =>
                void run("Link related record", async () => {
                  await onAddRelation({
                    targetRecordId: relatedRecordId.trim(),
                    relationType,
                    reason: relationReason.trim(),
                    status: relationStatus,
                    validFrom: relationValidFrom || null,
                    validTo: relationValidTo || null,
                  });
                  setRelatedRecordId("");
                  setRelationReason("");
                  setRelationStatus("candidate");
                  setRelationValidFrom("");
                  setRelationValidTo("");
                })
              }
            >
              Link record
            </button>
            {record.recordType === "adr" ? (
              <button
                type="button"
                disabled={busy || !relatedRecordId.trim() || !relationReason.trim()}
                onClick={() => {
                  if (confirmGovernedAction(`Supersede “${record.title}”?`))
                    void run("Supersede ADR", async () => {
                      await onSupersede(relatedRecordId.trim(), relationReason.trim());
                      setRelatedRecordId("");
                      setRelationReason("");
                    });
                }}
              >
                Supersede with ADR
              </button>
            ) : null}
          </div>
        ) : null}
      </article>

      {canManage && canCreateBacklog ? (
        <article className="card">
          <h3>Create Architecture Backlog item</h3>
          <p className="subtle">
            Use this when an interim governance arrangement needs a traceable correction.
          </p>
          <div className="filter-grid">
            <TextField
              label="Business key"
              value={backlogKey}
              disabled={busy}
              onChange={setBacklogKey}
            />
            <TextField
              label="Title"
              value={backlogTitle}
              disabled={busy}
              onChange={setBacklogTitle}
            />
            <TextField
              label="Priority (required)"
              value={backlogPriority}
              disabled={busy}
              onChange={setBacklogPriority}
            />
            <label className="field">
              <span>Target date (required)</span>
              <input
                type="date"
                disabled={busy}
                value={backlogTargetDate}
                onChange={(event) => setBacklogTargetDate(event.target.value)}
              />
            </label>
          </div>
          <TextAreaField
            label="Pending correction"
            value={pendingCorrection}
            disabled={busy}
            onChange={setPendingCorrection}
            rows={3}
          />
          <TextAreaField
            label="Reason"
            value={backlogReason}
            disabled={busy}
            onChange={setBacklogReason}
            rows={3}
          />
          <button
            type="button"
            disabled={
              busy ||
              !backlogKey.trim() ||
              !backlogTitle.trim() ||
              !pendingCorrection.trim() ||
              !backlogReason.trim() ||
              !backlogPriority.trim() ||
              !backlogTargetDate
            }
            onClick={() =>
              void run("Create Architecture Backlog item", async () => {
                await onCreateBacklog({
                  businessKey: backlogKey.trim(),
                  title: backlogTitle.trim(),
                  pendingCorrection: pendingCorrection.trim(),
                  reason: backlogReason.trim(),
                  priority: backlogPriority.trim(),
                  targetDate: backlogTargetDate,
                });
                setBacklogKey("");
                setBacklogTitle("");
                setPendingCorrection("");
                setBacklogReason("");
                setBacklogPriority("");
                setBacklogTargetDate("");
              })
            }
          >
            Create Architecture Backlog item
          </button>
        </article>
      ) : null}

      {message ? (
        <p className="state-message" role="status">
          {message}
        </p>
      ) : null}
    </section>
  );
}
