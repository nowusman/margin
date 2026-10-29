import React, { useEffect, useMemo, useState } from "react";
import { SelectField, TextAreaField, TextField } from "../../components/forms";
import { EmptyState, ErrorState, LoadingState } from "../../components/layout/States";
import { DataTable } from "../../components/tables";
import { entityCatalogClient } from "../../lib/apiEntities";
import { knowledgeClient } from "../../lib/apiKnowledge";
import { shortId } from "../../lib/display";
import { useCommand, useLoad } from "../../lib/hooks";
import { AppRoute } from "./types";

type Navigate = (_route: AppRoute, _params?: Record<string, string>) => void;

type EntityCatalogPageProps = {
  onNavigate: Navigate;
};

const lifecycleOptions = ["", "draft", "active", "superseded", "retired"];

export function EntityCatalogPage({ onNavigate }: EntityCatalogPageProps): React.JSX.Element {
  const command = useCommand();
  const domains = useLoad("entity-catalog-domains", () => knowledgeClient.listDomains("read"));
  const ontology = useLoad("entity-catalog-ontology", entityCatalogClient.ontology);
  const [domainId, setDomainId] = useState("");
  const [ontologyType, setOntologyType] = useState("");
  const [lifecycleStatus, setLifecycleStatus] = useState("");
  const [alias, setAlias] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [preferredLabel, setPreferredLabel] = useState("");
  const [newType, setNewType] = useState("");
  const [newDomainId, setNewDomainId] = useState("");
  const [locale, setLocale] = useState("en");
  const [externalId, setExternalId] = useState("");
  const [definition, setDefinition] = useState("");
  const [definitionIdeaBlockId, setDefinitionIdeaBlockId] = useState("");
  const [ownerUserId, setOwnerUserId] = useState("");
  const [stewardUserId, setStewardUserId] = useState("");
  const [selectedMentionId, setSelectedMentionId] = useState("");
  const [resolutionEntityId, setResolutionEntityId] = useState("");
  const [resolutionReason, setResolutionReason] = useState("");

  const filters = useMemo(
    () => ({
      domainId: domainId || undefined,
      ontologyType: ontologyType || undefined,
      lifecycleStatus: lifecycleStatus || undefined,
      alias: alias.trim() || undefined,
      unresolved: true,
      limit: 250,
    }),
    [alias, domainId, lifecycleStatus, ontologyType]
  );
  const catalog = useLoad(`entity-catalog-${JSON.stringify(filters)}`, () =>
    entityCatalogClient.list(filters)
  );
  const domainNameById = useMemo(
    () => new Map((domains.data ?? []).map((domain) => [domain.id, domain.name])),
    [domains.data]
  );
  const typeOptions = ontology.data?.entityTypes ?? [];
  const domainOptions = ["", ...(domains.data ?? []).map((domain) => domain.id)];

  useEffect(() => {
    if (!newType && typeOptions[0]) setNewType(typeOptions[0]);
  }, [newType, typeOptions]);

  const clearCreateForm = (): void => {
    setPreferredLabel("");
    setNewDomainId("");
    setLocale("en");
    setExternalId("");
    setDefinition("");
    setDefinitionIdeaBlockId("");
    setOwnerUserId("");
    setStewardUserId("");
  };

  const createEntity = async (): Promise<void | false> => {
    if (!preferredLabel.trim() || !newType) return false;
    const created = await entityCatalogClient.create({
      preferredLabel: preferredLabel.trim(),
      ontologyType: newType,
      domainId: newDomainId || null,
      locale: locale.trim() || "en",
      externalId: externalId.trim() || null,
      definition: definition.trim() || null,
      definitionIdeaBlockId: definitionIdeaBlockId.trim() || null,
      ownerUserId: ownerUserId.trim() || null,
      stewardUserId: stewardUserId.trim() || null,
      lifecycleStatus: "draft",
    });
    clearCreateForm();
    setCreateOpen(false);
    catalog.refresh();
    onNavigate("/knowledge/entities/:entityId", { entityId: created.id });
  };

  const resolveMention = async (): Promise<void | false> => {
    if (!selectedMentionId || !resolutionEntityId.trim() || !resolutionReason.trim()) return false;
    await entityCatalogClient.resolveMention(
      selectedMentionId,
      resolutionEntityId.trim(),
      resolutionReason.trim()
    );
    setSelectedMentionId("");
    setResolutionEntityId("");
    setResolutionReason("");
    catalog.refresh();
  };

  const domainLabel = (value: string | null): string => {
    if (!value) return "Global";
    return domainNameById.get(value) ?? value;
  };

  return (
    <section className="page-grid">
      <article className="card">
        <div className="listview-header">
          <div>
            <p className="eyebrow">Canonical Entity Catalog</p>
            <h2>Entities and governed terms</h2>
            <p className="subtle">
              {catalog.data?.count ?? 0} results · ontology{" "}
              {ontology.data?.ontologyVersion || "loading"}
            </p>
          </div>
          <button type="button" onClick={() => setCreateOpen(true)}>
            New entity
          </button>
        </div>
        <div className="filter-grid">
          <SelectField
            label="Domain"
            value={domainId}
            onChange={setDomainId}
            options={domainOptions}
          />
          <SelectField
            label="Lifecycle"
            value={lifecycleStatus}
            onChange={setLifecycleStatus}
            options={lifecycleOptions}
          />
          <TextField
            label="Ontology type"
            value={ontologyType}
            onChange={setOntologyType}
            placeholder="For example: system"
          />
          <TextField label="Alias" value={alias} onChange={setAlias} placeholder="Exact alias" />
        </div>
        {catalog.loading ? <LoadingState label="Loading canonical entities..." /> : null}
        <ErrorState error={catalog.error} />
        {catalog.data && catalog.data.items.length > 0 ? (
          <DataTable
            rows={catalog.data.items}
            emptyLabel="No canonical entities match these filters."
            getRowKey={(item) => item.id}
            columns={[
              {
                header: "Entity",
                render: (item) => (
                  <button
                    type="button"
                    className="table-link"
                    onClick={() =>
                      onNavigate("/knowledge/entities/:entityId", { entityId: item.id })
                    }
                  >
                    {item.preferredLabel}
                  </button>
                ),
                sortValue: (item) => item.preferredLabel,
              },
              {
                header: "Type",
                render: (item) => item.ontologyType,
                sortValue: (item) => item.ontologyType,
              },
              {
                header: "Domain",
                render: (item) => domainLabel(item.domainId),
                sortValue: (item) => domainLabel(item.domainId),
              },
              {
                header: "Lifecycle",
                render: (item) => <span className="badge">{item.lifecycleStatus}</span>,
                sortValue: (item) => item.lifecycleStatus,
              },
              {
                header: "Catalog version",
                render: (item) => String(item.catalogVersion),
                sortValue: (item) => item.catalogVersion,
              },
              { header: "ID", render: (item) => shortId(item.id), sortValue: (item) => item.id },
            ]}
          />
        ) : (
          !catalog.loading && <EmptyState label="No canonical entities match these filters." />
        )}
      </article>

      <article className="card">
        <div className="listview-header">
          <div>
            <p className="eyebrow">Resolution review</p>
            <h2>Unresolved mentions</h2>
          </div>
          <span className="badge tone-warning">{catalog.data?.unresolvedCount ?? 0}</span>
        </div>
        {catalog.data?.unresolvedMentions.length ? (
          <DataTable
            rows={catalog.data.unresolvedMentions}
            emptyLabel="No unresolved mentions."
            getRowKey={(item) => item.id}
            columns={[
              { header: "Mention", render: (item) => item.text, sortValue: (item) => item.text },
              {
                header: "Proposed type",
                render: (item) => item.proposedType,
                sortValue: (item) => item.proposedType,
              },
              {
                header: "Domain",
                render: (item) => domainLabel(item.domainId),
                sortValue: (item) => domainLabel(item.domainId),
              },
              {
                header: "Candidates",
                render: (item) => item.candidateEntityIds.map(shortId).join(", ") || "None",
              },
              {
                header: "Action",
                render: (item) => (
                  <button
                    type="button"
                    onClick={() => {
                      setSelectedMentionId(item.id);
                      setResolutionEntityId(item.candidateEntityIds[0] ?? "");
                    }}
                  >
                    Resolve
                  </button>
                ),
              },
            ]}
          />
        ) : (
          <EmptyState label="No unresolved mentions require review." />
        )}
        {selectedMentionId ? (
          <div className="stacked-form">
            <h3>Resolve mention {shortId(selectedMentionId)}</h3>
            <TextField
              label="Canonical Entity ID"
              value={resolutionEntityId}
              onChange={setResolutionEntityId}
            />
            <TextAreaField
              label="Decision reason"
              value={resolutionReason}
              onChange={setResolutionReason}
              rows={3}
            />
            <div className="actions-row">
              <button
                type="button"
                disabled={command.busy || !resolutionEntityId.trim() || !resolutionReason.trim()}
                onClick={() => command.runCommand("Resolve entity mention", resolveMention)}
              >
                Apply resolution
              </button>
              <button
                type="button"
                onClick={() => setSelectedMentionId("")}
                disabled={command.busy}
              >
                Cancel
              </button>
            </div>
          </div>
        ) : null}
        {command.message ? <p role="status">{command.message}</p> : null}
      </article>

      {createOpen ? (
        <div className="modal-backdrop">
          <div
            className="modal-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="new-entity-title"
          >
            <div className="modal-header">
              <h2 id="new-entity-title">New canonical Entity</h2>
              <button type="button" onClick={() => setCreateOpen(false)} disabled={command.busy}>
                Close
              </button>
            </div>
            <TextField
              label="Preferred label"
              value={preferredLabel}
              onChange={setPreferredLabel}
            />
            <div className="filter-grid">
              <SelectField
                label="Ontology type"
                value={newType}
                onChange={setNewType}
                options={typeOptions}
              />
              <SelectField
                label="Domain"
                value={newDomainId}
                onChange={setNewDomainId}
                options={domainOptions}
              />
              <TextField label="Locale" value={locale} onChange={setLocale} />
              <TextField
                label="Immutable external ID"
                value={externalId}
                onChange={setExternalId}
              />
            </div>
            <TextAreaField
              label="Definition"
              value={definition}
              onChange={setDefinition}
              rows={4}
            />
            <TextField
              label="Definition IdeaBlock ID"
              value={definitionIdeaBlockId}
              onChange={setDefinitionIdeaBlockId}
              placeholder="Required when a definition is provided"
            />
            <div className="filter-grid">
              <TextField label="Owner user ID" value={ownerUserId} onChange={setOwnerUserId} />
              <TextField
                label="Steward user ID"
                value={stewardUserId}
                onChange={setStewardUserId}
              />
            </div>
            <p className="subtle">
              New entities start as drafts and require review before use in extraction.
            </p>
            <div className="actions-row">
              <button
                type="button"
                disabled={command.busy || !preferredLabel.trim() || !newType}
                onClick={() => command.runCommand("Create canonical entity", createEntity)}
              >
                Create draft
              </button>
              <button type="button" onClick={() => setCreateOpen(false)} disabled={command.busy}>
                Cancel
              </button>
            </div>
            {command.message ? <p role="status">{command.message}</p> : null}
            <ErrorState error={ontology.error || domains.error} />
          </div>
        </div>
      ) : null}
    </section>
  );
}
