import React, { useEffect, useMemo, useState } from "react";
import { TextAreaField, TextField } from "../../components/forms";
import { IconAction } from "../../components/layout/IconAction";
import { EmptyState, ErrorState, LoadingState } from "../../components/layout/States";
import { DataTable } from "../../components/tables";
import { knowledgeClient } from "../../lib/apiKnowledge";
import type { DomainSummary } from "../../lib/apiTypes";
import { confirmGovernedAction, shortId, slugFromTitle } from "../../lib/display";
import { useCommand, useLoad } from "../../lib/hooks";
import { AppRoute } from "./types";

type Navigate = (_route: AppRoute, _params?: Record<string, string>) => void;

export function DomainKnowledgeView({
  params,
  onNavigate,
}: {
  params: Record<string, string>;
  onNavigate: Navigate;
}): React.JSX.Element {
  const command = useCommand();
  const domains = useLoad("knowledge-domains", () => knowledgeClient.listDomains("read"));
  const [domainId, setDomainId] = useState("");
  const [domainName, setDomainName] = useState("");
  const [domainOwner, setDomainOwner] = useState("");
  const [domainSteward, setDomainSteward] = useState("");
  const [domainDescription, setDomainDescription] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [existing, setExisting] = useState(false);

  useEffect(() => {
    if (!params.domainId || !domains.data) return;
    const domain = domains.data.find((item) => item.id === params.domainId);
    if (domain) openDomainRecord(domain);
  }, [domains.data, params.domainId]);

  const metrics = useMemo(() => {
    const rows = domains.data ?? [];
    return {
      active: rows.filter((domain) => (domain.status || "active").toLowerCase() === "active")
        .length,
      owned: rows.filter((domain) => Boolean(domain.owner)).length,
      stewarded: rows.filter((domain) => Boolean(domain.steward)).length,
      total: rows.length,
    };
  }, [domains.data]);

  const resetForm = (): void => {
    setDomainId("");
    setDomainName("");
    setDomainOwner("");
    setDomainSteward("");
    setDomainDescription("");
  };

  const openNewDomain = (): void => {
    resetForm();
    setExisting(false);
    setDialogOpen(true);
    onNavigate("/knowledge/domains");
  };

  const openDomainRecord = (domain: DomainSummary): void => {
    setExisting(true);
    setDomainId(domain.id);
    setDomainName(domain.name);
    setDomainOwner(domain.owner || "");
    setDomainSteward(domain.steward || "");
    setDomainDescription(domain.description || "");
    setDialogOpen(true);
  };

  const closeDialog = (): void => {
    setDialogOpen(false);
    resetForm();
    onNavigate("/knowledge/domains");
  };

  const saveDomain = async (): Promise<void> => {
    const name = domainName.trim();
    await knowledgeClient.saveDomain({
      id: domainId.trim() || slugFromTitle(name, "domain"),
      name,
      owner: domainOwner.trim() || undefined,
      steward: domainSteward.trim() || undefined,
      description: domainDescription.trim() || undefined,
    });
    closeDialog();
    domains.refresh();
  };

  const deleteDomain = async (domain: DomainSummary): Promise<void | false> => {
    if (!confirmGovernedAction(`Delete domain ${domain.name || domain.id}?`)) return false;
    await knowledgeClient.deleteDomain(domain.id);
    if (domain.id === domainId) closeDialog();
    domains.refresh();
  };

  const deleteCurrentDomain = async (): Promise<void | false> => {
    const domain: DomainSummary = {
      id: domainId,
      name: domainName,
      owner: domainOwner,
      steward: domainSteward,
      description: domainDescription,
      status: "active",
    };
    return deleteDomain(domain);
  };

  return (
    <section className="knowledge-domain-page">
      <div className="knowledge-hero">
        <div>
          <p className="eyebrow">Knowledge governance</p>
          <h2>{metrics.total}</h2>
          <p>Domain ownership and stewardship for the Entity Catalog.</p>
        </div>
        <div className="source-hero-metrics" aria-label="Domain summary">
          <span>
            <strong>{metrics.owned}</strong>
            Owned domains
          </span>
          <span>
            <strong>{metrics.stewarded}</strong>
            Stewarded domains
          </span>
          <span>
            <strong>{metrics.active}</strong>
            Active domains
          </span>
        </div>
      </div>
      <article className="card knowledge-domain-table-card">
        <div className="listview-header">
          <div>
            <p className="eyebrow">Domains</p>
            <h2>Architecture domains</h2>
          </div>
          <IconAction label="Create domain" icon="add" onClick={openNewDomain} />
        </div>
        {domains.loading ? <LoadingState label="Loading domains..." /> : null}
        <ErrorState error={domains.error} />
        {domains.data && domains.data.length > 0 ? (
          <div className="domain-table">
            <DataTable
              rows={domains.data}
              emptyLabel="No domains available."
              getRowKey={(row) => row.id}
              density="compact"
              columns={[
                {
                  header: "Domain",
                  render: (row) => (
                    <span className="source-title-cell">
                      <strong>{row.name}</strong>
                      <span>{shortId(row.id)}</span>
                    </span>
                  ),
                  sortValue: (row) => row.name,
                },
                {
                  header: "Technical key",
                  render: (row) => <span className="technical-id">{row.id}</span>,
                  sortValue: (row) => row.id,
                },
                {
                  header: "Owner",
                  render: (row) => row.owner || "Unowned",
                  sortValue: (row) => row.owner,
                },
                {
                  header: "Steward",
                  render: (row) => row.steward || "Unassigned",
                  sortValue: (row) => row.steward,
                },
                {
                  header: "Description",
                  render: (row) => row.description || "Not recorded",
                  sortValue: (row) => row.description,
                },
                {
                  header: "Status",
                  render: (row) => (
                    <span className="badge tone-success">{row.status || "active"}</span>
                  ),
                  sortValue: (row) => row.status,
                },
                {
                  header: "Action",
                  render: (row) => (
                    <div className="listview-actions">
                      <IconAction
                        label={`View/edit domain ${row.name}`}
                        icon="edit"
                        onClick={() =>
                          onNavigate("/knowledge/domains/:domainId", { domainId: row.id })
                        }
                      />
                      <IconAction
                        label={`Delete domain ${row.name}`}
                        icon="delete"
                        disabled={command.busy}
                        onClick={() => command.runCommand("Delete domain", () => deleteDomain(row))}
                      />
                    </div>
                  ),
                },
              ]}
            />
          </div>
        ) : (
          !domains.loading && <EmptyState label="No domains available." />
        )}
        {command.message ? <p role="status">{command.message}</p> : null}
      </article>
      {dialogOpen ? (
        <div className="modal-backdrop">
          <div
            className="modal-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="domain-record-title"
          >
            <div className="modal-header">
              <h2 id="domain-record-title">{existing ? "Domain record" : "Create domain"}</h2>
              <button type="button" onClick={closeDialog}>
                Close
              </button>
            </div>
            <TextField
              disabled={existing}
              label="Technical key"
              value={domainId}
              onChange={setDomainId}
              placeholder="Generated from domain name when blank"
            />
            <TextField label="Domain name" value={domainName} onChange={setDomainName} />
            <TextField label="Owner" value={domainOwner} onChange={setDomainOwner} />
            <TextField label="Steward" value={domainSteward} onChange={setDomainSteward} />
            <TextAreaField
              label="Description"
              value={domainDescription}
              onChange={setDomainDescription}
              rows={4}
            />
            <div className="actions-row">
              <button
                type="button"
                disabled={command.busy || !domainName.trim()}
                onClick={() => command.runCommand("Save domain", saveDomain)}
              >
                Save
              </button>
              <button
                type="button"
                disabled={command.busy || !existing}
                onClick={() => command.runCommand("Delete domain", deleteCurrentDomain)}
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
