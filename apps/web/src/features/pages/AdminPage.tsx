import React, { useEffect, useMemo, useState } from "react";
import { TextField } from "../../components/forms";
import { IconAction } from "../../components/layout/IconAction";
import { EmptyState, ErrorState, LoadingState } from "../../components/layout/States";
import { DataTable } from "../../components/tables";
import { adminClient } from "../../lib/apiAdminSources";
import type { ModelRegistryItem } from "../../lib/apiTypes";
import { confirmGovernedAction, shortId, technicalIdLabel } from "../../lib/display";
import { useCommand, useLoad } from "../../lib/hooks";
import {
  emptyModelForm,
  modelFormPayload,
  modelToForm,
  parseCsv,
  type AccessProbeResult,
  type ModelDialogMode,
  type ModelFormState,
} from "./AdminPage.helpers";
import { AdminModelDialog } from "./AdminModelDialog";
import { AppRoute } from "./types";

export type AdminPageProps = {
  route: AppRoute;
};

export function AdminPage({ route }: AdminPageProps): React.JSX.Element {
  const command = useCommand();
  const [selectedModelId, setSelectedModelId] = useState("");
  const [modelForm, setModelForm] = useState<ModelFormState | null>(null);
  const [modelDialogMode, setModelDialogMode] = useState<ModelDialogMode | null>(null);

  const [accessProbeUser, setAccessProbeUser] = useState("alice");
  const [accessUser, setAccessUser] = useState("");
  const [accessScope, setAccessScope] = useState("all");
  const [accessProbeResult, setAccessProbeResult] = useState<AccessProbeResult | null>(null);
  const [accessDialogOpen, setAccessDialogOpen] = useState(false);
  const [accessGroups, setAccessGroups] = useState("");
  const [accessDomains, setAccessDomains] = useState("");
  const [accessCategories, setAccessCategories] = useState("");
  const [accessSensitivities, setAccessSensitivities] = useState("");
  const [accessExisting, setAccessExisting] = useState(false);

  const models = useLoad("admin-models", adminClient.models, {
    enabled: route === "/admin/models",
  });
  const modelHealth = useLoad("admin-model-health", adminClient.modelHealth, {
    enabled: route === "/admin/models",
  });
  const modelConfigurationChecks = useLoad(
    "admin-model-configuration-checks",
    adminClient.modelConfigurationChecks,
    {
      enabled: route === "/admin/models",
    }
  );

  const systemHealth = useLoad("admin-system-health", adminClient.systemHealth, {
    enabled: route === "/admin/system",
  });
  const auditEvents = useLoad("admin-audit-events", adminClient.auditEvents, {
    enabled: route === "/admin/system",
  });
  const access = useLoad("admin-access", adminClient.access, {
    enabled: route === "/admin/access",
  });

  const selectedModel = useMemo(
    () => models.data?.find((model) => model.id === selectedModelId) ?? null,
    [models.data, selectedModelId]
  );
  useEffect(() => {
    if (modelDialogMode === "add") {
      return;
    }
    if (!selectedModel) {
      setModelForm(null);
      return;
    }
    setModelForm(modelToForm(selectedModel));
  }, [modelDialogMode, selectedModel]);

  const updateModelForm = (key: keyof ModelFormState, value: string): void => {
    setModelForm((current) => {
      if (!current) return current;
      return { ...current, [key]: value };
    });
  };

  const openAddModelDialog = (): void => {
    setModelForm(emptyModelForm());
    setModelDialogMode("add");
  };

  const openEditModelDialog = (model: ModelRegistryItem): void => {
    setSelectedModelId(model.id);
    setModelForm(modelToForm(model));
    setModelDialogMode("edit");
  };

  const closeModelDialog = (): void => {
    setModelDialogMode(null);
    if (selectedModel) {
      setModelForm(modelToForm(selectedModel));
    }
  };

  const refreshModelAdministration = (): void => {
    models.refresh();
    modelHealth.refresh();
    modelConfigurationChecks.refresh();
  };

  const openAccessDialog = (profile?: NonNullable<typeof access.data>[number]): void => {
    setAccessExisting(Boolean(profile));
    setAccessUser(profile?.user ?? "");
    setAccessGroups(profile?.groups.join(", ") ?? "");
    setAccessDomains(profile?.domains.join(", ") ?? "");
    setAccessCategories(profile?.categories.join(", ") ?? "");
    setAccessSensitivities(profile?.sensitivities.join(", ") ?? "");
    setAccessDialogOpen(true);
  };

  const saveAccessProfile = async (): Promise<void> => {
    await adminClient.saveAccessProfile(
      accessUser.trim(),
      {
        groups: parseCsv(accessGroups),
        domains: parseCsv(accessDomains),
        categories: parseCsv(accessCategories),
        sensitivities: parseCsv(accessSensitivities),
      },
      accessExisting
    );
    setAccessDialogOpen(false);
    access.refresh();
  };

  const deleteAccessProfile = async (user: string): Promise<void | false> => {
    if (!confirmGovernedAction(`Delete access profile "${user}"?`)) return false;
    await adminClient.deleteAccessProfile(user);
    setAccessDialogOpen(false);
    access.refresh();
  };

  if (route === "/admin/models") {
    return (
      <section className="knowledge-list-layout">
        <article className="card">
          <div className="listview-header">
            <h2>Model administration</h2>
            <IconAction label="Create model" icon="add" onClick={openAddModelDialog} />
          </div>
          {models.loading ? <LoadingState label="Loading model registry..." /> : null}
          <ErrorState error={models.error} />
          {models.data && models.data.length > 0 ? (
            <div className="table-scroll">
              <DataTable
                rows={models.data}
                emptyLabel="No models configured."
                getRowKey={(row) => row.id}
                columns={[
                  {
                    header: "Model",
                    render: (row) => row.displayName,
                    sortValue: (row) => row.displayName,
                  },
                  {
                    header: "Purpose",
                    render: (row) => row.purpose,
                    sortValue: (row) => row.purpose,
                  },
                  {
                    header: "Runtime",
                    render: (row) => row.runtime,
                    sortValue: (row) => row.runtime,
                  },
                  {
                    header: "Model name",
                    render: (row) => row.modelName || "-",
                    sortValue: (row) => row.modelName,
                  },
                  {
                    header: "Endpoint",
                    render: (row) => (
                      <details className="technical-details">
                        <summary>{row.endpointUrl ? "Configured" : "Not configured"}</summary>
                        <p className="technical-id">
                          {row.endpointUrl || "No endpoint configured"}
                        </p>
                      </details>
                    ),
                    sortValue: (row) => row.endpointUrl,
                  },
                  {
                    header: "Health test",
                    render: (row) => (
                      <details className="technical-details">
                        <summary>{row.lastTestStatus || "not tested"}</summary>
                        <p className="technical-id">
                          {row.lastTestDetail || "No health-test result recorded."}
                          {row.lastTestLatencyMs !== null ? ` (${row.lastTestLatencyMs} ms)` : ""}
                        </p>
                      </details>
                    ),
                    sortValue: (row) => row.lastTestStatus || "",
                  },
                  {
                    header: "Secret",
                    render: (row) => (row.secretConfigured ? "configured" : "not set"),
                  },
                  {
                    header: "Capabilities",
                    render: (row) =>
                      Object.keys(row.capabilities || {}).length > 0 ? "configured" : "default",
                  },
                  {
                    header: "License",
                    render: (row) => row.license,
                    sortValue: (row) => row.license,
                  },
                  { header: "Status", render: (row) => row.status, sortValue: (row) => row.status },
                  {
                    header: "Actions",
                    render: (row) => (
                      <div className="listview-actions">
                        <IconAction
                          label={`View/edit model ${row.displayName}`}
                          icon="edit"
                          onClick={() => openEditModelDialog(row)}
                        />
                        <IconAction
                          label={`Test model ${row.displayName}`}
                          icon="approve"
                          disabled={command.busy}
                          onClick={() =>
                            command.runCommand("Test model", async () => {
                              const result = await adminClient.testModel(row.id);
                              refreshModelAdministration();
                              if (result.status !== "healthy") {
                                throw new Error(
                                  result.detail || `Model health test returned ${result.status}`
                                );
                              }
                            })
                          }
                        />
                        <IconAction
                          label={`Delete model ${row.displayName}`}
                          icon="delete"
                          disabled={command.busy || row.status === "active"}
                          onClick={() =>
                            command.runCommand("Delete model", async () => {
                              if (!confirmGovernedAction(`Delete model "${row.displayName}"?`))
                                return false;
                              await adminClient.deleteModel(row.id);
                              refreshModelAdministration();
                            })
                          }
                        />
                      </div>
                    ),
                  },
                ]}
              />
            </div>
          ) : (
            !models.loading && <EmptyState label="No models available." />
          )}
          {modelDialogMode && modelForm ? (
            <AdminModelDialog
              busy={command.busy}
              configuredSecret={Boolean(selectedModel?.secretConfigured)}
              form={modelForm}
              mode={modelDialogMode}
              onCancel={closeModelDialog}
              onChange={updateModelForm}
              onSave={() =>
                command.runCommand("Save model settings", async () => {
                  if (modelDialogMode === "add") {
                    const createdModel = await adminClient.addModel(modelFormPayload(modelForm));
                    if (createdModel) setSelectedModelId(createdModel.id);
                  } else if (selectedModel) {
                    await adminClient.updateModel(selectedModel.id, modelFormPayload(modelForm));
                  }
                  closeModelDialog();
                  refreshModelAdministration();
                })
              }
            />
          ) : null}
          {command.message ? <p role="status">{command.message}</p> : null}
        </article>

        <article className="card">
          <h2>Model operations</h2>
          {modelHealth.loading ? <LoadingState label="Loading model health..." /> : null}
          <ErrorState error={modelHealth.error} />
          {modelHealth.data && modelHealth.data.length > 0
            ? modelHealth.data.map((item) => (
                <p key={`${item.provider}-${item.detail}`}>
                  {item.provider}: {item.status} ({item.detail})
                </p>
              ))
            : !modelHealth.loading && <EmptyState label="No model health records." />}
          <h3>Configuration checks</h3>
          {modelConfigurationChecks.data && modelConfigurationChecks.data.length > 0 ? (
            modelConfigurationChecks.data.map((item) => (
              <p key={item.id}>
                Configuration check {shortId(item.id)} -{" "}
                {item.configured ? "configured" : "not configured"}
              </p>
            ))
          ) : (
            <EmptyState label="No compliance records." />
          )}
        </article>
      </section>
    );
  }

  if (route === "/admin/access") {
    return (
      <section className="knowledge-list-layout">
        <article className="card">
          <div className="listview-header">
            <h2>Users and access</h2>
            <IconAction
              label="Create access profile"
              icon="add"
              onClick={() => openAccessDialog()}
            />
          </div>
          {access.loading ? <LoadingState label="Loading access records..." /> : null}
          <ErrorState error={access.error} />
          {access.data && access.data.length > 0 ? (
            <DataTable
              rows={access.data}
              emptyLabel="No access records available."
              getRowKey={(row) => row.user}
              columns={[
                { header: "User", render: (row) => row.user, sortValue: (row) => row.user },
                {
                  header: "Groups",
                  render: (row) => row.groups.join(", ") || "none",
                  sortValue: (row) => row.groups.join(", "),
                },
                {
                  header: "Domains",
                  render: (row) => row.domains.join(", ") || "none",
                  sortValue: (row) => row.domains.join(", "),
                },
                {
                  header: "Categories",
                  render: (row) => row.categories.join(", ") || "none",
                  sortValue: (row) => row.categories.join(", "),
                },
                {
                  header: "Sensitivities",
                  render: (row) => row.sensitivities.join(", ") || "none",
                  sortValue: (row) => row.sensitivities.join(", "),
                },
                {
                  header: "Action",
                  render: (row) => (
                    <div className="listview-actions">
                      <IconAction
                        label={`View/edit access ${row.user}`}
                        icon="edit"
                        onClick={() => openAccessDialog(row)}
                      />
                      <IconAction
                        label={`Delete access ${row.user}`}
                        icon="delete"
                        disabled={command.busy}
                        onClick={() =>
                          command.runCommand("Delete access profile", () =>
                            deleteAccessProfile(row.user)
                          )
                        }
                      />
                    </div>
                  ),
                },
              ]}
            />
          ) : (
            !access.loading && <EmptyState label="No access records available." />
          )}
        </article>
        <article className="card">
          <h2>Access probe</h2>
          <div className="filter-grid">
            <TextField label="User" value={accessProbeUser} onChange={setAccessProbeUser} />
            <TextField
              label="Scope"
              value={accessScope}
              onChange={setAccessScope}
              placeholder="all or domain:OSS,sensitivity:internal"
            />
          </div>
          <button
            type="button"
            disabled={command.busy}
            onClick={() =>
              command.runCommand("Run access check", async () => {
                const result = await adminClient.accessCheck({
                  user: accessProbeUser,
                  scope: accessScope,
                });
                setAccessProbeResult(result);
              })
            }
          >
            Run access check
          </button>
          {accessProbeResult ? (
            <>
              <p>User: {accessProbeResult.user}</p>
              <p>Allowed: {accessProbeResult.canSee.join(", ") || "none"}</p>
              <p>Denied: {accessProbeResult.denied.join(", ") || "none"}</p>
              <p>{accessProbeResult.decisionTrace}</p>
            </>
          ) : (
            <EmptyState label="No access probe executed." />
          )}
          {command.message ? <p role="status">{command.message}</p> : null}
        </article>
        {accessDialogOpen ? (
          <div className="modal-backdrop">
            <div
              className="modal-dialog"
              role="dialog"
              aria-modal="true"
              aria-labelledby="access-record-title"
            >
              <div className="modal-header">
                <h2 id="access-record-title">
                  {accessExisting ? "Access profile" : "Create access profile"}
                </h2>
                <button type="button" onClick={() => setAccessDialogOpen(false)}>
                  Close
                </button>
              </div>
              <TextField
                disabled={accessExisting}
                label="User"
                value={accessUser}
                onChange={setAccessUser}
              />
              <TextField
                label="Groups (comma separated)"
                value={accessGroups}
                onChange={setAccessGroups}
              />
              <TextField
                label="Domains (comma separated)"
                value={accessDomains}
                onChange={setAccessDomains}
              />
              <TextField
                label="Categories (comma separated)"
                value={accessCategories}
                onChange={setAccessCategories}
              />
              <TextField
                label="Sensitivities (comma separated)"
                value={accessSensitivities}
                onChange={setAccessSensitivities}
              />
              <div className="actions-row">
                <button
                  type="button"
                  disabled={command.busy || !accessUser.trim()}
                  onClick={() => command.runCommand("Save access profile", saveAccessProfile)}
                >
                  Save
                </button>
                <button
                  type="button"
                  disabled={command.busy || !accessExisting}
                  onClick={() =>
                    command.runCommand("Delete access profile", () =>
                      deleteAccessProfile(accessUser)
                    )
                  }
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

  if (route === "/admin/system") {
    return (
      <section className="page-grid">
        <article className="card">
          <h2>System health checks</h2>
          {systemHealth.loading ? <LoadingState label="Loading system health..." /> : null}
          <ErrorState error={systemHealth.error} />
          {systemHealth.data ? (
            <>
              <p>Status: {systemHealth.data.status}</p>
              <p>Database: {systemHealth.data.database}</p>
              <p>Converter dependencies: {systemHealth.data.converterDependencies}</p>
              <p>Object storage configuration: {systemHealth.data.objectStorage}</p>
              <p>Redis configuration: {systemHealth.data.redis}</p>
            </>
          ) : (
            !systemHealth.loading && <EmptyState label="No system health data." />
          )}
        </article>
        <article className="card">
          <h2>Audit log</h2>
          {auditEvents.loading ? <LoadingState label="Loading audit events..." /> : null}
          <ErrorState error={auditEvents.error} />
          {auditEvents.data && auditEvents.data.length > 0
            ? auditEvents.data.map((event) => (
                <div key={event.id}>
                  <p>
                    {event.actor} - {event.action} - {event.at}
                  </p>
                  <details className="technical-details">
                    <summary>Technical details</summary>
                    <p className="technical-id">Target: {event.target}</p>
                    <p className="technical-id">{technicalIdLabel(event.id)}</p>
                  </details>
                </div>
              ))
            : !auditEvents.loading && <EmptyState label="No audit events available." />}
        </article>
      </section>
    );
  }

  return <EmptyState label="Choose a supported administration section." />;
}
