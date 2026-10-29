import React, { useEffect, useMemo, useState } from "react";
import { SelectField, TextAreaField } from "../../components/forms";
import { IconAction } from "../../components/layout/IconAction";
import { EmptyState, ErrorState, LoadingState } from "../../components/layout/States";
import { DataTable } from "../../components/tables";
import { knowledgeClient } from "../../lib/apiKnowledge";
import { entityCatalogClient } from "../../lib/apiEntities";
import type {
  IdeaBlockSummary,
  ManualIdeaBlockEntityInput,
  ManualIdeaBlockRelationshipInput,
} from "../../lib/apiTypes";
import { confirmGovernedAction, shortId, technicalIdLabel } from "../../lib/display";
import { useCommand, useLoad } from "../../lib/hooks";
import { useRecordView } from "../../lib/useRecordView";
import { uniqueNonEmpty } from "./SourcesPage.helpers";
import { AppRoute } from "./types";

type Navigate = (_route: AppRoute, _params?: Record<string, string>) => void;

type StatusAction = {
  status: string;
  action: string;
  icon: "approve" | "reject" | "candidate";
};

/**
 * The transitions the governance state machine allows, per current status.
 *
 * A claim is only offered the moves the corpus permits, so a reader is never
 * shown an action that the catalogue will refuse.
 */
const STATUS_ACTIONS: Record<string, StatusAction[]> = {
  candidate: [
    { status: "verified", action: "Approve IdeaBlock", icon: "approve" },
    { status: "rejected", action: "Reject IdeaBlock", icon: "reject" },
  ],
  verified: [{ status: "rejected", action: "Reject IdeaBlock", icon: "reject" }],
  rejected: [{ status: "candidate", action: "Mark candidate", icon: "candidate" }],
  superseded: [{ status: "candidate", action: "Mark candidate", icon: "candidate" }],
};

const manualIdeaBlockTypeOptions = [
  "statement",
  "relationship",
  "fact",
  "decision",
  "risk",
  "waiver",
  "technical_debt",
];

const parseManualEntities = (value: string): ManualIdeaBlockEntityInput[] =>
  value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [name, kind, role = "context", externalId] = line.split("|").map((item) => item.trim());
      if (!name || !kind) throw new Error("Each entity row must be: name | kind | role");
      return { name, kind, role, externalId };
    });

const parseManualRelationships = (value: string): ManualIdeaBlockRelationshipInput[] =>
  value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [source, relationType, target, confidence = "extracted"] = line
        .split("|")
        .map((item) => item.trim());
      if (!source || !relationType || !target) {
        throw new Error("Each relationship row must be: source | relation | target");
      }
      return { source, relationType, target, confidence };
    });

export function IdeaBlockKnowledgeView({
  params,
  onNavigate,
}: {
  params: Record<string, string>;
  onNavigate: Navigate;
}): React.JSX.Element {
  const command = useCommand();
  const ideablocks = useLoad("ideablocks", knowledgeClient.listIdeaBlocks);
  const domains = useLoad("ideablock-domains", () => knowledgeClient.listDomains("write"));
  const categories = useLoad("ideablock-categories", () =>
    entityCatalogClient.list({ ontologyType: "category", lifecycleStatus: "active", limit: 250 })
  );
  const [selectedIdeaBlockId, setSelectedIdeaBlockId] = useState(params.ideaBlockId || "");
  const [selectedIdeaBlockIds, setSelectedIdeaBlockIds] = useState<Set<string>>(new Set());
  const [detailOpen, setDetailOpen] = useState(Boolean(params.ideaBlockId));
  const [newDialogOpen, setNewDialogOpen] = useState(false);
  const [newStatement, setNewStatement] = useState("");
  const [newDomainId, setNewDomainId] = useState("");
  const [newCategory, setNewCategory] = useState("Design Document");
  const [newType, setNewType] = useState("statement");
  const [newConfidence, setNewConfidence] = useState("inferred");
  const [newSourceQuote, setNewSourceQuote] = useState("");
  const [newEntityRows, setNewEntityRows] = useState("");
  const [newRelationshipRows, setNewRelationshipRows] = useState("");
  const detail = useLoad(
    `ideablock-${selectedIdeaBlockId || "none"}`,
    () => knowledgeClient.getIdeaBlock(selectedIdeaBlockId),
    { enabled: Boolean(selectedIdeaBlockId) }
  );
  const [editedStatement, setEditedStatement] = useState("");

  useRecordView("idea_block", detailOpen ? selectedIdeaBlockId : "");

  useEffect(() => {
    if (!params.ideaBlockId) return;
    setSelectedIdeaBlockId(params.ideaBlockId);
    setDetailOpen(true);
  }, [params.ideaBlockId]);

  useEffect(() => {
    setEditedStatement(detail.data?.statement ?? "");
  }, [detail.data?.id, detail.data?.statement]);

  useEffect(() => {
    const visibleIds = new Set((ideablocks.data ?? []).map((row) => row.id));
    setSelectedIdeaBlockIds(
      (current) => new Set(Array.from(current).filter((id) => visibleIds.has(id)))
    );
  }, [ideablocks.data]);

  useEffect(() => {
    if (!newDomainId && domains.data?.[0]) setNewDomainId(domains.data[0].id);
  }, [domains.data, newDomainId]);

  const categoryOptions = useMemo(
    () => uniqueNonEmpty((categories.data?.items ?? []).map((item) => item.preferredLabel)),
    [categories.data]
  );

  useEffect(() => {
    if (categoryOptions.length === 0 || newCategory) return;
    setNewCategory(categoryOptions[0]);
  }, [categoryOptions, newCategory]);

  const resetNewIdeaBlockForm = (): void => {
    setNewStatement("");
    setNewCategory(categoryOptions[0] ?? "Design Document");
    setNewType("statement");
    setNewConfidence("inferred");
    setNewSourceQuote("");
    setNewEntityRows("");
    setNewRelationshipRows("");
  };

  const openNewIdeaBlock = (): void => {
    resetNewIdeaBlockForm();
    setNewDomainId(domains.data?.[0]?.id ?? "");
    setNewDialogOpen(true);
  };

  const closeNewIdeaBlock = (): void => {
    setNewDialogOpen(false);
    resetNewIdeaBlockForm();
  };

  const saveNewIdeaBlock = async (): Promise<void | false> => {
    if (!newStatement.trim() || !newDomainId || !newType) return false;
    const created = await knowledgeClient.createIdeaBlock({
      statement: newStatement.trim(),
      domainId: newDomainId,
      category: newCategory,
      type: newType.trim(),
      confidence: newConfidence,
      sourceQuote: (newSourceQuote || newStatement).trim(),
      entities: parseManualEntities(newEntityRows),
      relationships: parseManualRelationships(newRelationshipRows),
    });
    ideablocks.refresh();
    setSelectedIdeaBlockId(created.id);
    setDetailOpen(true);
    setNewDialogOpen(false);
    resetNewIdeaBlockForm();
    onNavigate("/knowledge/ideablocks/:ideaBlockId", { ideaBlockId: created.id });
  };

  const openIdeaBlock = (row: IdeaBlockSummary): void => {
    setSelectedIdeaBlockId(row.id);
    setDetailOpen(true);
    onNavigate("/knowledge/ideablocks/:ideaBlockId", { ideaBlockId: row.id });
  };

  const closeIdeaBlock = (): void => {
    setDetailOpen(false);
    setSelectedIdeaBlockId("");
    onNavigate("/knowledge/ideablocks");
  };

  const updateIdeaBlockStatus = async (
    row: IdeaBlockSummary,
    status: string
  ): Promise<void | false> => {
    if (
      status === "verified" &&
      !confirmGovernedAction(
        `Approve this claim as verified? Evidence quote: ${row.evidence[0]?.sourceQuote || "not returned"}`
      )
    ) {
      return false;
    }
    if (
      status === "rejected" &&
      !confirmGovernedAction(
        `Reject this claim? Evidence quote: ${row.evidence[0]?.sourceQuote || "not returned"}`
      )
    ) {
      return false;
    }
    await knowledgeClient.updateIdeaBlockStatus(row.id, status);
    ideablocks.refresh();
    if (selectedIdeaBlockId === row.id) {
      detail.refresh();
    }
  };

  const deleteIdeaBlockRecord = async (row: IdeaBlockSummary): Promise<void | false> => {
    if (!confirmGovernedAction(`Delete IdeaBlock ${shortId(row.id)} and its relationships?`))
      return false;
    await knowledgeClient.deleteIdeaBlock(row.id);
    ideablocks.refresh();
    setSelectedIdeaBlockIds((current) => {
      const next = new Set(current);
      next.delete(row.id);
      return next;
    });
    if (selectedIdeaBlockId === row.id) {
      closeIdeaBlock();
    }
  };

  const toggleIdeaBlockSelection = (ideaBlockId: string): void => {
    setSelectedIdeaBlockIds((current) => {
      const next = new Set(current);
      if (next.has(ideaBlockId)) {
        next.delete(ideaBlockId);
      } else {
        next.add(ideaBlockId);
      }
      return next;
    });
  };

  const selectAllIdeaBlocks = (): void => {
    setSelectedIdeaBlockIds(new Set((ideablocks.data ?? []).map((row) => row.id)));
  };

  const updateSelectedIdeaBlocks = async (status: string, label: string): Promise<void | false> => {
    const ids = Array.from(selectedIdeaBlockIds);
    if (ids.length === 0) return false;
    if (
      (status === "verified" || status === "rejected") &&
      !confirmGovernedAction(`${label} ${ids.length} selected IdeaBlocks?`)
    ) {
      return false;
    }
    await Promise.all(ids.map((id) => knowledgeClient.updateIdeaBlockStatus(id, status)));
    setSelectedIdeaBlockIds(new Set());
    ideablocks.refresh();
    if (selectedIdeaBlockId && ids.includes(selectedIdeaBlockId)) {
      detail.refresh();
    }
  };
  const ideaBlockMetrics = useMemo(() => {
    const rows = ideablocks.data ?? [];
    return {
      total: rows.length,
      verified: rows.filter((row) => row.status === "verified").length,
      candidate: rows.filter((row) => row.status === "candidate").length,
      rejected: rows.filter((row) => row.status === "rejected").length,
    };
  }, [ideablocks.data]);

  return (
    <section className="knowledge-ideablock-page">
      <div className="knowledge-hero knowledge-ideablock-hero">
        <div>
          <p className="eyebrow">Extracted Knowledge</p>
          <h2>{ideaBlockMetrics.total}</h2>
          <p>Evidence-backed claims ready for review, approval, rejection, or refinement.</p>
        </div>
        <div className="source-hero-metrics" aria-label="IdeaBlock summary">
          <span>
            <strong>{ideaBlockMetrics.verified}</strong>
            Verified
          </span>
          <span>
            <strong>{ideaBlockMetrics.candidate}</strong>
            Candidates
          </span>
          <span>
            <strong>{selectedIdeaBlockIds.size}</strong>
            Selected
          </span>
        </div>
      </div>
      <div className="source-kpis">
        <article className="kpi-card accent-violet">
          <strong>{ideaBlockMetrics.total}</strong>
          <span>Total claims</span>
        </article>
        <article className="kpi-card accent-teal">
          <strong>{ideaBlockMetrics.verified}</strong>
          <span>Verified</span>
        </article>
        <article className="kpi-card accent-magenta">
          <strong>{ideaBlockMetrics.candidate}</strong>
          <span>Candidates</span>
        </article>
        <article className="kpi-card accent-amber">
          <strong>{ideaBlockMetrics.rejected}</strong>
          <span>Rejected</span>
        </article>
      </div>
      <article className="card ideablock-list-card">
        <div className="listview-header">
          <div>
            <p className="eyebrow">Claims</p>
            <h2>IdeaBlocks</h2>
          </div>
          <div className="listview-actions">
            <button type="button" onClick={openNewIdeaBlock}>
              New Ideablock
            </button>
            <button type="button" onClick={selectAllIdeaBlocks} disabled={!ideablocks.data?.length}>
              Select all
            </button>
            <button
              type="button"
              onClick={() => setSelectedIdeaBlockIds(new Set())}
              disabled={selectedIdeaBlockIds.size === 0}
            >
              Clear
            </button>
            <IconAction
              label={`Approve ${selectedIdeaBlockIds.size} selected IdeaBlocks`}
              icon="approve"
              disabled={command.busy || selectedIdeaBlockIds.size === 0}
              onClick={() =>
                command.runCommand("Approve selected IdeaBlocks", () =>
                  updateSelectedIdeaBlocks("verified", "Approve")
                )
              }
            />
            <IconAction
              label={`Reject ${selectedIdeaBlockIds.size} selected IdeaBlocks`}
              icon="reject"
              disabled={command.busy || selectedIdeaBlockIds.size === 0}
              onClick={() =>
                command.runCommand("Reject selected IdeaBlocks", () =>
                  updateSelectedIdeaBlocks("rejected", "Reject")
                )
              }
            />
          </div>
        </div>
        {ideablocks.loading ? <LoadingState label="Loading IdeaBlocks..." /> : null}
        <ErrorState error={ideablocks.error} />
        {ideablocks.data && ideablocks.data.length > 0 ? (
          <div className="ideablock-table">
            <DataTable
              rows={ideablocks.data}
              emptyLabel="No IdeaBlocks available."
              getRowKey={(row) => row.id}
              density="compact"
              columns={[
                {
                  header: "Select",
                  render: (row) => (
                    <input
                      type="checkbox"
                      aria-label={`Select IdeaBlock ${shortId(row.id)}`}
                      checked={selectedIdeaBlockIds.has(row.id)}
                      onChange={() => toggleIdeaBlockSelection(row.id)}
                    />
                  ),
                },
                {
                  header: "Statement",
                  render: (row) => (
                    <span className="source-title-cell">
                      <strong>{row.statement}</strong>
                      <span>{shortId(row.id)}</span>
                    </span>
                  ),
                  sortValue: (row) => row.statement,
                },
                {
                  header: "Status",
                  render: (row) => (
                    <span
                      className={
                        row.status === "verified"
                          ? "badge tone-success"
                          : row.status === "rejected"
                            ? "badge tone-danger"
                            : "badge tone-info"
                      }
                    >
                      {row.status}
                    </span>
                  ),
                  sortValue: (row) => row.status,
                },
                {
                  header: "Confidence",
                  render: (row) => <span className="badge">{row.confidence || "unknown"}</span>,
                  sortValue: (row) => row.confidence,
                },
                {
                  header: "Action",
                  render: (row) => (
                    <div className="listview-actions">
                      <IconAction
                        label={`Open IdeaBlock ${shortId(row.id)}`}
                        icon="view"
                        onClick={() => openIdeaBlock(row)}
                      />
                      {STATUS_ACTIONS[row.status]?.map((action) => (
                        <IconAction
                          key={action.status}
                          label={`${action.action} ${shortId(row.id)}`}
                          icon={action.icon}
                          disabled={command.busy}
                          onClick={() =>
                            command.runCommand(action.action, () =>
                              updateIdeaBlockStatus(row, action.status)
                            )
                          }
                        />
                      ))}
                      <IconAction
                        label={`Delete IdeaBlock ${shortId(row.id)}`}
                        icon="delete"
                        disabled={command.busy}
                        onClick={() =>
                          command.runCommand("Delete IdeaBlock", () => deleteIdeaBlockRecord(row))
                        }
                      />
                    </div>
                  ),
                },
              ]}
            />
          </div>
        ) : (
          !ideablocks.loading && <EmptyState label="No IdeaBlocks available." />
        )}
        {command.message ? <p role="status">{command.message}</p> : null}
      </article>
      {newDialogOpen ? (
        <div className="modal-backdrop">
          <div
            className="modal-dialog ideablock-detail-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="new-ideablock-title"
          >
            <div className="modal-header">
              <h2 id="new-ideablock-title">New Ideablock</h2>
              <button type="button" onClick={closeNewIdeaBlock}>
                Close
              </button>
            </div>
            <TextAreaField
              label="Statement"
              value={newStatement}
              rows={4}
              onChange={setNewStatement}
              placeholder="Service A depends on Service B."
            />
            <div className="filter-grid">
              <SelectField
                label="Domain"
                value={newDomainId}
                onChange={setNewDomainId}
                options={(domains.data ?? []).map((domain) => domain.id)}
              />
              <SelectField
                label="Category"
                value={newCategory}
                onChange={setNewCategory}
                options={categoryOptions}
              />
              <SelectField
                label="Type"
                value={newType}
                onChange={setNewType}
                options={manualIdeaBlockTypeOptions}
              />
              <SelectField
                label="Confidence"
                value={newConfidence}
                onChange={setNewConfidence}
                options={["inferred", "ambiguous"]}
              />
            </div>
            <p>Status candidate</p>
            <TextAreaField
              label="Evidence quote"
              value={newSourceQuote}
              rows={3}
              onChange={setNewSourceQuote}
              placeholder="Defaults to the statement."
            />
            <TextAreaField
              label="Entities"
              value={newEntityRows}
              rows={4}
              onChange={setNewEntityRows}
              placeholder="Service A | component | subject&#10;Service B | component | object"
            />
            <TextAreaField
              label="Relationships"
              value={newRelationshipRows}
              rows={3}
              onChange={setNewRelationshipRows}
              placeholder="Service A | depends_on | Service B | extracted"
            />
            <details className="technical-details">
              <summary>Technical details</summary>
              <p className="technical-id">Source section and span are created when saved.</p>
              <pre className="code-block">
                {JSON.stringify(
                  {
                    extraction_method: "manual",
                    status: "candidate",
                    source_type: "manual_ideablock",
                  },
                  null,
                  2
                )}
              </pre>
            </details>
            <div className="actions-row">
              <button
                type="button"
                disabled={command.busy || !newStatement.trim() || !newDomainId || !newType}
                onClick={() => command.runCommand("Create IdeaBlock", saveNewIdeaBlock)}
              >
                Save
              </button>
              <button type="button" onClick={closeNewIdeaBlock} disabled={command.busy}>
                Cancel
              </button>
            </div>
            {command.message ? <p role="status">{command.message}</p> : null}
            <ErrorState error={domains.error} />
            <ErrorState error={categories.error} />
          </div>
        </div>
      ) : null}
      {detailOpen ? (
        <div className="modal-backdrop">
          <div
            className="modal-dialog ideablock-detail-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="ideablock-detail-title"
          >
            <div className="modal-header">
              <h2 id="ideablock-detail-title">IdeaBlock detail</h2>
              <button type="button" onClick={closeIdeaBlock}>
                Close
              </button>
            </div>
            {detail.loading ? <LoadingState label="Loading IdeaBlock..." /> : null}
            <ErrorState error={detail.error} />
            {detail.data ? (
              <>
                <p>{detail.data.statement}</p>
                <p>
                  Category {detail.data.category} - Type {detail.data.type}
                </p>
                <p>
                  Confidence {detail.data.confidence} - Status {detail.data.status}
                </p>
                <p>
                  Evidence source:{" "}
                  {detail.data.evidence[0]?.sourceQuote
                    ? "Source quote available"
                    : "No quote returned"}
                </p>
                <p>Quote: {detail.data.evidence[0]?.sourceQuote}</p>
                <details className="technical-details">
                  <summary>Technical details</summary>
                  <p className="technical-id">{technicalIdLabel(detail.data.id)}</p>
                  <p className="technical-id">
                    Source element: {detail.data.evidence[0]?.sourceElementId || "Unavailable"}
                  </p>
                  <p className="technical-id">
                    Revision: {detail.data.currentRevisionId || "Unavailable"}
                  </p>
                  <pre className="code-block">{JSON.stringify(detail.data.metadata, null, 2)}</pre>
                </details>
                <label className="field">
                  <span>Edit statement</span>
                  <textarea
                    value={editedStatement}
                    rows={4}
                    onChange={(event) => setEditedStatement(event.target.value)}
                  />
                </label>
                <div className="actions-row">
                  <button
                    type="button"
                    onClick={() =>
                      onNavigate("/knowledge/relationships/ideablock/:ideaBlockId", {
                        ideaBlockId: detail.data!.id,
                      })
                    }
                  >
                    Explore semantic relationships
                  </button>
                  <button
                    type="button"
                    disabled={command.busy || !editedStatement.trim()}
                    onClick={() =>
                      command.runCommand("Save statement", () =>
                        knowledgeClient.updateIdeaBlock(detail.data!.id, {
                          statement: editedStatement.trim(),
                        })
                      )
                    }
                  >
                    Save statement
                  </button>
                  <button
                    type="button"
                    disabled={command.busy}
                    onClick={() =>
                      confirmGovernedAction(
                        `Approve this claim as verified? Evidence quote: ${detail.data!.evidence[0]?.sourceQuote || "not returned"}`
                      ) &&
                      command.runCommand("Approve IdeaBlock", () =>
                        knowledgeClient.updateIdeaBlockStatus(detail.data!.id, "verified")
                      )
                    }
                  >
                    Approve
                  </button>
                  <button
                    type="button"
                    disabled={command.busy}
                    onClick={() =>
                      confirmGovernedAction(
                        `Reject this claim? Evidence quote: ${detail.data!.evidence[0]?.sourceQuote || "not returned"}`
                      ) &&
                      command.runCommand("Reject IdeaBlock", () =>
                        knowledgeClient.updateIdeaBlockStatus(detail.data!.id, "rejected")
                      )
                    }
                  >
                    Reject
                  </button>
                  <button
                    type="button"
                    disabled={command.busy}
                    onClick={() =>
                      command.runCommand("Mark candidate", () =>
                        knowledgeClient.updateIdeaBlockStatus(detail.data!.id, "candidate")
                      )
                    }
                  >
                    Edit status
                  </button>
                </div>
                {command.message ? <p role="status">{command.message}</p> : null}
                <p className="subtle">
                  Actions: approve, reject, edit statement, merge, split, create relationship, flag
                  contradiction, assign review.
                </p>
              </>
            ) : (
              !detail.loading && (
                <EmptyState
                  label={
                    selectedIdeaBlockId
                      ? `Claim ${shortId(selectedIdeaBlockId)} was not found.`
                      : "Select a claim to view details."
                  }
                />
              )
            )}
          </div>
        </div>
      ) : null}
    </section>
  );
}
