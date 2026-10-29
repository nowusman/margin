import React, { useEffect, useMemo, useState } from "react";
import { EvidenceCard, EvidenceCardData } from "../../components/evidence/EvidenceCard";
import { SelectField, TextAreaField, TextField } from "../../components/forms";
import { EmptyState, ErrorState, LoadingState } from "../../components/layout/States";
import { DataTable } from "../../components/tables";
import { knowledgeClient } from "../../lib/apiKnowledge";
import { entityCatalogClient } from "../../lib/apiEntities";
import { shortId } from "../../lib/display";
import { useCommand, useLoad } from "../../lib/hooks";
import { useRecordView } from "../../lib/useRecordView";
import { AppRoute } from "./types";

type Navigate = (_route: AppRoute, _params?: Record<string, string>) => void;

type EntityDetailPageProps = {
  entityId: string;
  onNavigate: Navigate;
};

export function EntityDetailPage({
  entityId,
  onNavigate,
}: EntityDetailPageProps): React.JSX.Element {
  const command = useCommand();
  useRecordView("entity", entityId);
  const detail = useLoad(`entity-detail-${entityId}`, () => entityCatalogClient.detail(entityId));
  const ontology = useLoad("entity-detail-ontology", entityCatalogClient.ontology);
  const definitionBlock = useLoad(
    `entity-definition-${detail.data?.definitionIdeaBlockId ?? "none"}`,
    () => knowledgeClient.getIdeaBlock(detail.data?.definitionIdeaBlockId ?? ""),
    { enabled: Boolean(detail.data?.definitionIdeaBlockId) }
  );
  const [preferredLabel, setPreferredLabel] = useState("");
  const [ontologyType, setOntologyType] = useState("");
  const [locale, setLocale] = useState("en");
  const [definition, setDefinition] = useState("");
  const [definitionIdeaBlockId, setDefinitionIdeaBlockId] = useState("");
  const [ownerUserId, setOwnerUserId] = useState("");
  const [stewardUserId, setStewardUserId] = useState("");
  const [updateReason, setUpdateReason] = useState("");
  const [aliasValue, setAliasValue] = useState("");
  const [aliasReason, setAliasReason] = useState("");
  const [actionTargetId, setActionTargetId] = useState("");
  const [actionReason, setActionReason] = useState("");
  const [retireReason, setRetireReason] = useState("");

  useEffect(() => {
    if (!detail.data) return;
    setPreferredLabel(detail.data.preferredLabel);
    setOntologyType(detail.data.ontologyType);
    setLocale(detail.data.locale);
    setDefinition(detail.data.definition ?? "");
    setDefinitionIdeaBlockId(detail.data.definitionIdeaBlockId ?? "");
    setOwnerUserId(detail.data.ownerUserId ?? "");
    setStewardUserId(detail.data.stewardUserId ?? "");
  }, [detail.data?.id]);

  const saveUpdates = async (): Promise<void | false> => {
    if (!updateReason.trim() || !preferredLabel.trim() || !ontologyType) return false;
    await entityCatalogClient.update(entityId, {
      preferredLabel: preferredLabel.trim(),
      ontologyType,
      locale: locale.trim() || "en",
      definition: definition.trim() || null,
      definitionIdeaBlockId: definitionIdeaBlockId.trim() || null,
      ownerUserId: ownerUserId.trim() || null,
      stewardUserId: stewardUserId.trim() || null,
      reason: updateReason.trim(),
    });
    setUpdateReason("");
    detail.refresh();
  };

  const addAlias = async (): Promise<void | false> => {
    if (!aliasValue.trim() || !aliasReason.trim()) return false;
    await entityCatalogClient.addAlias(entityId, aliasValue.trim(), aliasReason.trim());
    setAliasValue("");
    setAliasReason("");
    detail.refresh();
  };

  const deprecateAlias = async (aliasId: string): Promise<void | false> => {
    if (!aliasReason.trim()) return false;
    await entityCatalogClient.deprecateAlias(entityId, aliasId, aliasReason.trim());
    setAliasReason("");
    detail.refresh();
  };

  const approve = async (): Promise<void | false> => {
    if (!actionReason.trim()) return false;
    await entityCatalogClient.approve(entityId, actionReason.trim());
    setActionReason("");
    detail.refresh();
  };

  const merge = async (): Promise<void | false> => {
    if (!actionTargetId.trim() || !actionReason.trim()) return false;
    await entityCatalogClient.merge(entityId, actionTargetId.trim(), actionReason.trim());
    onNavigate("/knowledge/entities/:entityId", { entityId: actionTargetId.trim() });
  };

  const supersede = async (): Promise<void | false> => {
    if (!actionTargetId.trim() || !actionReason.trim()) return false;
    await entityCatalogClient.supersede(entityId, actionTargetId.trim(), actionReason.trim());
    setActionReason("");
    detail.refresh();
  };

  const retire = async (): Promise<void | false> => {
    if (!retireReason.trim()) return false;
    await entityCatalogClient.retire(entityId, retireReason.trim());
    setRetireReason("");
    detail.refresh();
  };

  const definitionEvidence = useMemo<EvidenceCardData | null>(() => {
    const block = definitionBlock.data;
    const evidence = block?.evidence[0];
    if (!block || !evidence) return null;
    return {
      sourceTitle: "Ontology source",
      pageLabel: evidence.sourcePageId ?? "Not provided",
      elementLabel: evidence.sourceElementId,
      quote: evidence.sourceQuote,
      ideaBlockId: block.id,
      extractionMethod: "source-backed definition",
      confidence: block.confidence,
      verificationStatus: block.status,
      freshness: "current",
      sensitivity: "internal",
    };
  }, [definitionBlock.data]);

  if (detail.loading) return <LoadingState label="Loading canonical Entity..." />;
  if (detail.error) return <ErrorState error={detail.error} />;
  if (!detail.data) return <EmptyState label="This canonical Entity was not found." />;

  const record = detail.data;
  const typeOptions = ontology.data?.entityTypes ?? [record.ontologyType];
  const editable = ["draft", "active"].includes(record.lifecycleStatus);

  return (
    <section className="page-grid">
      <article className="card">
        <div className="listview-header">
          <div>
            <p className="eyebrow">Canonical Entity</p>
            <h2>{record.preferredLabel}</h2>
            <p className="subtle">
              {record.ontologyType} · {record.lifecycleStatus} · catalog version{" "}
              {record.catalogVersion}
            </p>
          </div>
          <button type="button" onClick={() => onNavigate("/knowledge/entities")}>
            Back to catalog
          </button>
        </div>
        <dl className="detail-list">
          <dt>Canonical ID</dt>
          <dd className="technical-id">{record.id}</dd>
          <dt>Ontology version</dt>
          <dd>{record.ontologyVersion}</dd>
          <dt>Domain</dt>
          <dd>{record.domainId ?? "Global"}</dd>
          <dt>Locale</dt>
          <dd>{record.locale}</dd>
          <dt>Immutable external ID</dt>
          <dd>{record.externalId ?? "Not assigned"}</dd>
          <dt>Superseded by</dt>
          <dd>{record.supersededByEntityId ?? "Not superseded"}</dd>
        </dl>
      </article>

      <article className="card">
        <h2>Catalog fields</h2>
        <div className="filter-grid">
          <TextField
            disabled={!editable}
            label="Preferred label"
            value={preferredLabel}
            onChange={setPreferredLabel}
          />
          <SelectField
            disabled={!editable}
            label="Ontology type"
            value={ontologyType}
            onChange={setOntologyType}
            options={typeOptions}
          />
          <TextField disabled={!editable} label="Locale" value={locale} onChange={setLocale} />
          <TextField
            disabled={!editable}
            label="Owner user ID"
            value={ownerUserId}
            onChange={setOwnerUserId}
          />
          <TextField
            disabled={!editable}
            label="Steward user ID"
            value={stewardUserId}
            onChange={setStewardUserId}
          />
        </div>
        <TextAreaField
          disabled={!editable}
          label="Definition"
          value={definition}
          onChange={setDefinition}
          rows={4}
        />
        <TextField
          disabled={!editable}
          label="Definition IdeaBlock ID"
          value={definitionIdeaBlockId}
          onChange={setDefinitionIdeaBlockId}
          placeholder="Required when a definition is provided"
        />
        <TextAreaField
          disabled={!editable}
          label="Update reason"
          value={updateReason}
          onChange={setUpdateReason}
          rows={2}
        />
        {!editable ? (
          <p className="subtle">Superseded and retired Entities are immutable catalog history.</p>
        ) : null}
        <button
          type="button"
          disabled={
            command.busy ||
            !editable ||
            !updateReason.trim() ||
            !preferredLabel.trim() ||
            !ontologyType
          }
          onClick={() => command.runCommand("Update canonical Entity", saveUpdates)}
        >
          Save changes
        </button>
      </article>

      <article className="card">
        <h2>Definition evidence</h2>
        {definitionBlock.loading ? <LoadingState label="Loading definition evidence..." /> : null}
        <ErrorState error={definitionBlock.error} />
        {definitionEvidence ? (
          <EvidenceCard
            evidence={definitionEvidence}
            onOpenIdeaBlock={() =>
              onNavigate("/knowledge/ideablocks/:ideaBlockId", {
                ideaBlockId: definitionEvidence.ideaBlockId ?? "",
              })
            }
          />
        ) : (
          !definitionBlock.loading && (
            <EmptyState label="No source-backed definition has been approved." />
          )
        )}
      </article>

      <article className="card">
        <h2>Aliases</h2>
        {record.aliases.length ? (
          <DataTable
            rows={record.aliases}
            emptyLabel="No aliases."
            getRowKey={(item) => item.id}
            columns={[
              { header: "Alias", render: (item) => item.alias, sortValue: (item) => item.alias },
              {
                header: "State",
                render: (item) => item.lifecycleStatus,
                sortValue: (item) => item.lifecycleStatus,
              },
              { header: "Locale", render: (item) => item.locale, sortValue: (item) => item.locale },
              {
                header: "Action",
                render: (item) =>
                  item.normalizedAlias === record.normalizedLabel ? (
                    <span className="subtle">Preferred label</span>
                  ) : (
                    <button
                      type="button"
                      disabled={
                        command.busy ||
                        !editable ||
                        item.lifecycleStatus !== "active" ||
                        !aliasReason.trim()
                      }
                      onClick={() =>
                        command.runCommand("Deprecate Entity alias", () => deprecateAlias(item.id))
                      }
                    >
                      Deprecate
                    </button>
                  ),
              },
            ]}
          />
        ) : (
          <EmptyState label="No aliases are attached to this Entity." />
        )}
        <div className="filter-grid">
          <TextField
            disabled={!editable}
            label="New alias"
            value={aliasValue}
            onChange={setAliasValue}
          />
          <TextField
            disabled={!editable}
            label="Alias action reason"
            value={aliasReason}
            onChange={setAliasReason}
          />
        </div>
        <button
          type="button"
          disabled={command.busy || !editable || !aliasValue.trim() || !aliasReason.trim()}
          onClick={() => command.runCommand("Add Entity alias", addAlias)}
        >
          Add alias
        </button>
      </article>

      <article className="card">
        <h2>Lifecycle and duplicate control</h2>
        <p className="subtle">
          Merge and supersession only accept compatible type and domain targets.
        </p>
        <TextField label="Target Entity ID" value={actionTargetId} onChange={setActionTargetId} />
        <TextAreaField
          label="Lifecycle action reason"
          value={actionReason}
          onChange={setActionReason}
          rows={2}
        />
        <div className="actions-row">
          {record.lifecycleStatus === "draft" ? (
            <button
              type="button"
              disabled={command.busy || !actionReason.trim()}
              onClick={() => command.runCommand("Approve Entity", approve)}
            >
              Approve draft
            </button>
          ) : null}
          <button
            type="button"
            disabled={
              command.busy ||
              !actionTargetId.trim() ||
              !actionReason.trim() ||
              record.lifecycleStatus !== "active"
            }
            onClick={() => command.runCommand("Merge duplicate Entity", merge)}
          >
            Merge into target
          </button>
          <button
            type="button"
            disabled={
              command.busy ||
              !actionTargetId.trim() ||
              !actionReason.trim() ||
              record.lifecycleStatus !== "active"
            }
            onClick={() => command.runCommand("Supersede Entity", supersede)}
          >
            Supersede with target
          </button>
        </div>
        <TextAreaField
          label="Retirement reason"
          value={retireReason}
          onChange={setRetireReason}
          rows={2}
        />
        <button
          type="button"
          disabled={
            command.busy ||
            !retireReason.trim() ||
            !["draft", "active"].includes(record.lifecycleStatus)
          }
          onClick={() => command.runCommand("Retire Entity", retire)}
        >
          Retire Entity
        </button>
      </article>

      <article className="card">
        <h2>Linked IdeaBlocks</h2>
        {record.ideaBlocks.length ? (
          <DataTable
            rows={record.ideaBlocks}
            emptyLabel="No IdeaBlocks are linked."
            getRowKey={(item) => `${item.ideaBlockId}-${item.role}`}
            columns={[
              {
                header: "IdeaBlock",
                render: (item) => (
                  <button
                    type="button"
                    className="table-link"
                    onClick={() =>
                      onNavigate("/knowledge/ideablocks/:ideaBlockId", {
                        ideaBlockId: item.ideaBlockId,
                      })
                    }
                  >
                    {shortId(item.ideaBlockId)}
                  </button>
                ),
              },
              { header: "Role", render: (item) => item.role },
              {
                header: "Current revision",
                render: (item) =>
                  item.currentRevisionId ? shortId(item.currentRevisionId) : "None",
              },
            ]}
          />
        ) : (
          <EmptyState label="No IdeaBlocks are linked to this Entity." />
        )}
      </article>

      <article className="card">
        <div className="listview-header">
          <h2>Relationships</h2>
          <button
            type="button"
            onClick={() => onNavigate("/knowledge/relationships/entity/:entityId", { entityId })}
          >
            Explore relationships
          </button>
        </div>
        <p className="subtle">
          Explore direct relationships, bounded dependency and impact paths, governance links, and
          authorized provenance in the relationship workspace.
        </p>
      </article>

      <article className="card">
        <h2>Governance records and source mentions</h2>
        <p>
          Governance links:{" "}
          {record.governanceRecords.map((item) => item.recordType).join(", ") || "None"}
        </p>
        {record.sourceMentions.length ? (
          <DataTable
            rows={record.sourceMentions}
            emptyLabel="No source mentions."
            getRowKey={(item) => item.mentionId}
            columns={[
              { header: "Mention", render: (item) => item.text, sortValue: (item) => item.text },
              { header: "Source element", render: (item) => shortId(item.sourceElementId) },
              { header: "Revision", render: (item) => shortId(item.ideaBlockRevisionId) },
            ]}
          />
        ) : (
          <EmptyState label="No resolved source mentions are linked." />
        )}
      </article>

      <article className="card">
        <h2>Resolution history</h2>
        {record.resolutionHistory.length ? (
          <DataTable
            rows={record.resolutionHistory}
            emptyLabel="No resolution history."
            getRowKey={(item) => item.id}
            columns={[
              { header: "Method", render: (item) => item.method, sortValue: (item) => item.method },
              {
                header: "Outcome",
                render: (item) => item.outcome,
                sortValue: (item) => item.outcome,
              },
              {
                header: "Record",
                render: (item) => (item.isCurrent ? "Current" : "Historical"),
                sortValue: (item) => (item.isCurrent ? "Current" : "Historical"),
              },
              { header: "Actor", render: (item) => item.decidedBy ?? "Automatic" },
              { header: "Reason", render: (item) => item.rationale ?? "Not recorded" },
              {
                header: "Timestamp",
                render: (item) => item.reviewedAt ?? item.createdAt ?? "Not recorded",
              },
            ]}
          />
        ) : (
          <EmptyState label="No resolution decisions reference this Entity." />
        )}
      </article>

      {command.message ? <p role="status">{command.message}</p> : null}
    </section>
  );
}
