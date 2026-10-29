import React, { useEffect, useMemo, useRef, useState } from "react";
import { DataTable } from "../../components/tables";
import { EvidenceCard } from "../../components/evidence/EvidenceCard";
import { CollapsibleCard } from "../../components/layout/CollapsibleCard";
import { EmptyState, ErrorState, LoadingState } from "../../components/layout/States";
import { SelectField, TextField } from "../../components/forms";
import { sourcesClient } from "../../lib/apiAdminSources";
import { jobsClient } from "../../lib/apiJobs";
import { knowledgeClient } from "../../lib/apiKnowledge";
import type { ExtractionReport } from "../../lib/apiTypes";
import { entityCatalogClient } from "../../lib/apiEntities";
import { useAuthSession } from "../../lib/auth";
import { confirmGovernedAction, shortId } from "../../lib/display";
import { useCommand, useLoad } from "../../lib/hooks";
import {
  acceptedUploadFormats,
  buildUploadSummary,
  defaultUploadState,
  detectSourceTypeOption,
  extractionModes,
  normalizeUploadDomain,
  sensitivityOptions,
  supportedUploadFormatLabel,
  totalWizardSteps,
  uniqueNonEmpty,
  uploadStepError,
  type UploadResponse,
  type UploadState,
} from "./SourcesPage.helpers";
import { AppRoute } from "./types";
import { SourceHistoryPanel } from "./SourceHistoryPanel";

export type SourcesPageProps = {
  params: Record<string, string>;
  onNavigate: (_route: AppRoute, _params?: Record<string, string>) => void;
};

type SourceRow = Awaited<ReturnType<typeof sourcesClient.list>>[number] & {
  attempts: number;
  state: string;
};

type SourceJobRow = Awaited<ReturnType<typeof jobsClient.list>>[number];

const jobTimestamp = (job: SourceJobRow): number => {
  const value = Date.parse(job.createdAt);
  return Number.isFinite(value) ? value : 0;
};

const formatGmtDateTime = (value: string): string => {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return "n/a";
  return `${date.toISOString().slice(0, 19).replace("T", " ")} GMT`;
};

const pipelineStages = [
  "received",
  "converting",
  "extracting",
  "resolving",
  "embedding",
  "completed",
];
const terminalStates = new Set(["review_required", "quarantined", "failed"]);

const stageStatus = (stage: string, current: string): "complete" | "active" | "pending" => {
  if (terminalStates.has(current)) {
    return stage === "completed" ? "active" : "complete";
  }
  const currentIndex = pipelineStages.indexOf(current);
  const stageIndex = pipelineStages.indexOf(stage);
  if (stageIndex < 0 || currentIndex < 0) return "pending";
  if (stageIndex < currentIndex) return "complete";
  if (stageIndex === currentIndex) return "active";
  return "pending";
};

export function SourcesPage({ params, onNavigate }: SourcesPageProps): React.JSX.Element {
  const sourceDetailRef = useRef<HTMLElement | null>(null);
  const sourceElementRef = useRef<HTMLElement | null>(null);
  const authSession = useAuthSession();
  const sources = useLoad("sources-list", sourcesClient.list);
  const jobs = useLoad("source-jobs", jobsClient.list);
  const filterDomains = useLoad("source-filter-domains", () => knowledgeClient.listDomains("read"));
  const uploadDomains = useLoad("source-upload-domains", () =>
    knowledgeClient.listDomains("write")
  );
  const categories = useLoad("source-categories", () =>
    entityCatalogClient.list({ ontologyType: "category", lifecycleStatus: "active", limit: 250 })
  );
  const [filters, setFilters] = useState({
    domain: "",
    category: "",
    sensitivity: "",
    status: "",
    owner: "",
  });
  const [wizardStep, setWizardStep] = useState(1);
  const [fileInputKey, setFileInputKey] = useState(0);
  const [upload, setUpload] = useState<UploadState>(defaultUploadState);
  const [uploadFailure, setUploadFailure] = useState<string | null>(null);
  const [lastUploadSummary, setLastUploadSummary] = useState<Array<[string, string]> | null>(null);
  const [selectedJobId, setSelectedJobId] = useState("");
  const command = useCommand();
  const currentStepError = uploadStepError(wizardStep, upload);
  const canDeleteSources = authSession.groups.some(
    (group) => group.replace(/_/g, "-") === "margin-admin"
  );
  const hasWritableDomains = (uploadDomains.data ?? []).length > 0;
  const uploadDomainError =
    !uploadDomains.loading && !hasWritableDomains
      ? "No writable domains available for upload."
      : null;
  const domainOptions = useMemo(() => {
    const writableDomains = uploadDomains.data ?? [];
    if (writableDomains.length === 0) return [];
    const options = [
      { value: "ALL", label: "All Domains" },
      ...writableDomains.map((domain) => ({
        value: domain.id,
        label: domain.name,
      })),
    ];
    if (upload.domainId && !options.some((option) => option.value === upload.domainId)) {
      options.push({ value: upload.domainId, label: `Domain ${shortId(upload.domainId)}` });
    }
    return options;
  }, [upload.domainId, uploadDomains.data]);
  const domainNameById = useMemo(
    () => new Map((filterDomains.data ?? []).map((domain) => [domain.id, domain.name])),
    [filterDomains.data]
  );
  const domainLabel = (domainId: string): string => {
    if (!domainId) return "Unassigned";
    if (domainId === "ALL") return "All Domains";
    return domainNameById.get(domainId) ?? `Domain ${shortId(domainId)}`;
  };
  const categoryOptions = useMemo(
    () => uniqueNonEmpty((categories.data?.items ?? []).map((item) => item.preferredLabel)),
    [categories.data]
  );
  useEffect(() => {
    if (categoryOptions.length === 0 || upload.category) return;
    setUpload((current) =>
      current.category ? current : { ...current, category: categoryOptions[0] }
    );
  }, [categoryOptions, upload.category]);
  const statusOptions = useMemo(
    () => uniqueNonEmpty((sources.data ?? []).map((source) => source.status)),
    [sources.data]
  );
  const ownerOptions = useMemo(
    () => uniqueNonEmpty((sources.data ?? []).map((source) => source.owner)),
    [sources.data]
  );

  const domainBreakdown = useMemo(() => {
    const counts = new Map<string, number>();
    (sources.data ?? []).forEach((source) => {
      const label = domainLabel(source.domainId);
      counts.set(label, (counts.get(label) ?? 0) + 1);
    });
    return Array.from(counts.entries())
      .map(([label, count]) => ({ label, count }))
      .sort((left, right) => right.count - left.count)
      .slice(0, 6);
  }, [domainNameById, sources.data]);
  const clearFilters = (): void => {
    setFilters({
      domain: "",
      category: "",
      sensitivity: "",
      status: "",
      owner: "",
    });
  };
  const hasFilters = Object.values(filters).some(Boolean);
  const jobsBySourceId = useMemo(() => {
    const latestJobs = new Map<string, Awaited<ReturnType<typeof jobsClient.list>>[number]>();
    (jobs.data ?? []).forEach((job) => {
      const existingJob = latestJobs.get(job.sourceDocumentId);
      if (!existingJob || jobTimestamp(job) >= jobTimestamp(existingJob)) {
        latestJobs.set(job.sourceDocumentId, job);
      }
    });
    return latestJobs;
  }, [jobs.data]);
  const attemptsBySourceId = useMemo(() => {
    const attempts = new Map<string, number>();
    (jobs.data ?? []).forEach((job) => {
      const count = Math.max(1, job.attempts);
      attempts.set(job.sourceDocumentId, (attempts.get(job.sourceDocumentId) ?? 0) + count);
    });
    return attempts;
  }, [jobs.data]);
  const sourceRows = useMemo<SourceRow[]>(
    () =>
      (sources.data ?? []).map((source) => {
        const latestJob = jobsBySourceId.get(source.id);
        return {
          ...source,
          state: latestJob?.state ?? "unknown",
          attempts: attemptsBySourceId.get(source.id) ?? 0,
        };
      }),
    [attemptsBySourceId, jobsBySourceId, sources.data]
  );
  const filteredSources = useMemo(() => {
    return sourceRows.filter((source) => {
      if (filters.domain && source.domainId !== filters.domain) return false;
      if (filters.category && source.category !== filters.category) return false;
      if (filters.sensitivity && source.sensitivity !== filters.sensitivity) return false;
      if (filters.status && source.status !== filters.status) return false;
      if (filters.owner && source.owner !== filters.owner) return false;
      return true;
    });
  }, [filters, sourceRows]);

  const selectedSourceId = params.sourceId || "";
  const selectedElementId = params.elementId || "";
  const scrollToSourceDetail = (): void => {
    const scrollAndFocus = (): void => {
      sourceDetailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      sourceDetailRef.current?.focus({ preventScroll: true });
    };
    if (typeof globalThis.requestAnimationFrame === "function") {
      globalThis.requestAnimationFrame(scrollAndFocus);
      return;
    }
    scrollAndFocus();
  };
  const sourceDetail = useLoad(
    `source-${selectedSourceId}`,
    () => sourcesClient.get(selectedSourceId),
    { enabled: Boolean(selectedSourceId) }
  );
  const sourceElements = useLoad(
    `source-elements-${selectedSourceId}`,
    () => sourcesClient.elements(selectedSourceId),
    { enabled: Boolean(selectedSourceId) }
  );
  const selectedSourceElement = useMemo(
    () => (sourceElements.data ?? []).find((element) => element.id === selectedElementId) ?? null,
    [selectedElementId, sourceElements.data]
  );
  useEffect(() => {
    if (selectedSourceId) {
      scrollToSourceDetail();
    }
  }, [selectedSourceId]);
  useEffect(() => {
    if (!selectedElementId || sourceElements.loading) return;
    const scrollAndFocus = (): void => {
      sourceElementRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      sourceElementRef.current?.focus({ preventScroll: true });
    };
    if (typeof globalThis.requestAnimationFrame === "function") {
      globalThis.requestAnimationFrame(scrollAndFocus);
      return;
    }
    scrollAndFocus();
  }, [selectedElementId, sourceElements.loading, selectedSourceElement]);

  const openSource = (sourceId: string): void => {
    if (sourceId === selectedSourceId) {
      onNavigate("/sources");
      return;
    }
    onNavigate("/sources/:sourceId", { sourceId });
  };
  const deleteSource = async (sourceId: string, title: string): Promise<void | false> => {
    if (
      !confirmGovernedAction(
        `Delete source "${title || shortId(sourceId)}" and its relationship information?`
      )
    ) {
      return false;
    }
    await sourcesClient.delete(sourceId);
    if (sourceId === selectedSourceId) {
      onNavigate("/sources");
    }
    sources.refresh();
    jobs.refresh();
  };
  const extractionReport = useLoad(
    `source-extraction-${selectedSourceId}`,
    async () => {
      if (!selectedSourceId) return null as unknown as ExtractionReport;
      return sourcesClient.extractionReport(selectedSourceId);
    },
    { enabled: Boolean(selectedSourceId) }
  );
  const ingestionReceipt = useLoad(
    `source-receipt-${selectedSourceId}`,
    () => sourcesClient.ingestionReceipt(selectedSourceId),
    { enabled: Boolean(selectedSourceId) }
  );
  const receipt = ingestionReceipt.data;
  const sourceSummaries = useMemo(() => {
    const allSources = sources.data ?? [];
    const ownerCount = new Set(allSources.map((source) => source.owner).filter(Boolean)).size;
    const reviewedCount = allSources.filter((source) => source.status === "reviewed").length;
    const failedJobCount = (jobs.data ?? []).filter((job) => job.state === "failed").length;
    return {
      total: allSources.length,
      filtered: filteredSources.length,
      domains: domainBreakdown.length,
      reviewed: reviewedCount,
      owners: ownerCount,
      failedJobs: failedJobCount,
    };
  }, [domainBreakdown.length, filteredSources.length, jobs.data, sources.data]);
  const sourceJobs = useMemo(
    () =>
      (jobs.data ?? [])
        .filter((item) => item.sourceDocumentId === selectedSourceId)
        .sort((left, right) => jobTimestamp(right) - jobTimestamp(left)),
    [jobs.data, selectedSourceId]
  );
  const selectedSourceAttempts = useMemo(
    () => sourceJobs.reduce((total, job) => total + Math.max(1, job.attempts), 0),
    [sourceJobs]
  );
  const latestSourceJob = sourceJobs[0] ?? null;
  const reprocessSourceLabel =
    latestSourceJob?.state === "failed" ? "Retry latest failed job" : "Reprocess source (new job)";
  const runSourceJobAction = (): void => {
    if (!selectedSourceId) return;
    const action =
      latestSourceJob?.state === "failed"
        ? () => jobsClient.retry(latestSourceJob.id)
        : () => sourcesClient.reprocess(selectedSourceId);
    command.runCommand(reprocessSourceLabel, action);
  };
  const jobDetail = useLoad(
    `source-job-${selectedJobId || "none"}`,
    () => jobsClient.detail(selectedJobId),
    { enabled: Boolean(selectedJobId), refreshMs: 5000 }
  );
  const selectedJobDetail =
    selectedJobId && jobDetail.data?.id === selectedJobId ? jobDetail.data : null;
  const stageRows = useMemo(() => {
    const currentState = selectedJobDetail?.state;
    if (!currentState) return [];
    return pipelineStages.map((stage) => ({
      stage,
      status: stageStatus(stage, currentState),
      detail: selectedJobDetail?.stageDetails.find((item) => item.stage === stage),
    }));
  }, [selectedJobDetail]);
  useEffect(() => {
    setSelectedJobId(latestSourceJob?.id ?? "");
  }, [latestSourceJob?.id]);

  const handleUpload = () => {
    const file = upload.file;
    if (uploadDomainError) return;
    if (uploadStepError(1, upload) || uploadStepError(2, upload)) return;
    if (!file) return;
    const filename = file.name;
    const domainId = normalizeUploadDomain(upload.domainId);
    const submittedSummary = buildUploadSummary(upload);
    setUploadFailure(null);
    command.runCommand("Start upload", () =>
      sourcesClient
        .upload(file, filename, {
          domain_id: domainId,
          category: upload.category || undefined,
          sensitivity: upload.sensitivity || undefined,
          source_uri: upload.sourceUri || undefined,
          source_version: upload.sourceVersion || undefined,
          source_owner: upload.owner || undefined,
          source_language: upload.language || undefined,
          extraction_mode: upload.extractionMode || undefined,
          extraction_priority: upload.extractionPriority || undefined,
          review_owner: upload.reviewOwner || undefined,
          source_type: upload.sourceType || undefined,
          source_date: upload.sourceDate || undefined,
        })
        .then((response) => {
          const uploadResponse = response as UploadResponse;
          const returnedSourceId =
            uploadResponse.source_document?.id ||
            uploadResponse.source_document_id ||
            uploadResponse.id;
          setLastUploadSummary(submittedSummary);
          setUpload({
            ...defaultUploadState,
            domainId: domainId ?? "",
            category: upload.category,
            sensitivity: upload.sensitivity,
            owner: upload.owner,
            reviewOwner: upload.reviewOwner,
            language: upload.language,
            extractionPriority: upload.extractionPriority,
            extractionMode: upload.extractionMode,
          });
          setFileInputKey((current) => current + 1);
          setWizardStep(1);
          sources.refresh();
          jobs.refresh();
          if (selectedSourceId) {
            sourceDetail.refresh();
            extractionReport.refresh();
          }
          if (returnedSourceId) {
            openSource(returnedSourceId);
          }
          return response;
        })
        .catch((reason: unknown) => {
          setUploadFailure(reason instanceof Error ? reason.message : "Source processing failed.");
          throw reason;
        })
    );
  };

  const selectUploadFile = (file: File | null): void => {
    const detectedType = file ? detectSourceTypeOption(file.name) : null;
    setLastUploadSummary(null);
    setUploadFailure(null);
    setUpload((current) => ({
      ...current,
      file,
      sourceType: detectedType?.label ?? current.sourceType,
    }));
  };

  const goToNextStep = (): void => {
    if (currentStepError) return;
    setWizardStep((current) => Math.min(totalWizardSteps, current + 1));
  };

  const goToPreviousStep = (): void => {
    setWizardStep((current) => Math.max(1, current - 1));
  };

  return (
    <section className="source-page">
      <div className="source-hero">
        <div>
          <p className="eyebrow">Source Library</p>
          <h2>{sourceSummaries.total}</h2>
          <p>Governed source documents feeding extraction, evidence, and canonical knowledge.</p>
        </div>
        <div className="source-hero-metrics" aria-label="Source summary">
          <span>
            <strong>{sourceSummaries.filtered}</strong>
            Matching filters
          </span>
          <span>
            <strong>{sourceSummaries.domains}</strong>
            Domains
          </span>
          <span>
            <strong>{sourceSummaries.reviewed}</strong>
            Reviewed
          </span>
        </div>
      </div>
      <div className="source-kpis">
        <article className="kpi-card accent-violet">
          <strong>{sourceSummaries.total}</strong>
          <span>Total sources</span>
        </article>
        <article className="kpi-card accent-teal">
          <strong>{sourceSummaries.domains}</strong>
          <span>Active domains</span>
        </article>
        <article className="kpi-card accent-magenta">
          <strong>{sourceSummaries.owners}</strong>
          <span>Owners</span>
        </article>
        <article className="kpi-card accent-amber">
          <strong>{sourceSummaries.failedJobs}</strong>
          <span>Failed jobs</span>
        </article>
      </div>
      {domainBreakdown.length > 0 ? (
        <div className="domain-strip">
          {domainBreakdown.map((domain) => (
            <button
              type="button"
              key={domain.label}
              className="domain-chip-card"
              onClick={() => {
                const match = (filterDomains.data ?? []).find((item) => item.name === domain.label);
                setFilters({ ...filters, domain: match?.id ?? "" });
              }}
            >
              <span>{domain.label}</span>
              <strong>{domain.count}</strong>
            </button>
          ))}
        </div>
      ) : null}
      <div className="source-workspace">
        <CollapsibleCard className="source-list-card" title="Source list and filters">
          <div className="source-section-head">
            <div>
              <p className="eyebrow">Catalog</p>
              <h3>Documents</h3>
            </div>
            {hasFilters ? (
              <button type="button" className="clear-filter-button" onClick={clearFilters}>
                Clear filters
              </button>
            ) : null}
          </div>
          <div className="filter-grid source-filter-grid">
            <label className="field">
              <span>Domain</span>
              <select
                value={filters.domain}
                onChange={(event) => setFilters({ ...filters, domain: event.target.value })}
              >
                <option value="">All domains</option>
                {(filterDomains.data ?? []).map((domain) => (
                  <option key={domain.id} value={domain.id}>
                    {domain.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Category</span>
              <select
                value={filters.category}
                onChange={(event) => setFilters({ ...filters, category: event.target.value })}
              >
                <option value="">All categories</option>
                {categoryOptions.map((category) => (
                  <option key={category} value={category}>
                    {category}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Sensitivity</span>
              <select
                value={filters.sensitivity}
                onChange={(event) => setFilters({ ...filters, sensitivity: event.target.value })}
              >
                <option value="">All sensitivities</option>
                {sensitivityOptions.map((sensitivity) => (
                  <option key={sensitivity} value={sensitivity}>
                    {sensitivity}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Status</span>
              <select
                value={filters.status}
                onChange={(event) => setFilters({ ...filters, status: event.target.value })}
              >
                <option value="">All statuses</option>
                {statusOptions.map((status) => (
                  <option key={status} value={status}>
                    {status}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Owner</span>
              <select
                value={filters.owner}
                onChange={(event) => setFilters({ ...filters, owner: event.target.value })}
              >
                <option value="">All owners</option>
                {ownerOptions.map((owner) => (
                  <option key={owner} value={owner}>
                    {owner}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {sources.loading ? <LoadingState label="Loading sources..." /> : null}
          <ErrorState error={sources.error} />
          {filteredSources.length > 0 ? (
            <DataTable
              rows={filteredSources}
              emptyLabel="No sources match filters."
              getRowKey={(row) => row.id}
              density="compact"
              columns={[
                {
                  header: "Title",
                  render: (row) => (
                    <span className="source-title-cell">
                      <strong>{row.title}</strong>
                      <span>{shortId(row.id)}</span>
                    </span>
                  ),
                  sortValue: (row) => row.title,
                },
                {
                  header: "Domain",
                  render: (row) => domainLabel(row.domainId),
                  sortValue: (row) => domainLabel(row.domainId),
                },
                {
                  header: "Category",
                  render: (row) => <span className="badge">{row.category || "Uncategorized"}</span>,
                  sortValue: (row) => row.category,
                },
                {
                  header: "Sensitivity",
                  render: (row) => (
                    <span className="badge tone-info">{row.sensitivity || "internal"}</span>
                  ),
                  sortValue: (row) => row.sensitivity,
                },
                { header: "State", render: (row) => row.state, sortValue: (row) => row.state },
                {
                  header: "Attempts",
                  render: (row) => row.attempts,
                  sortValue: (row) => row.attempts,
                },
                {
                  header: "Owner",
                  render: (row) => row.owner || "Unowned",
                  sortValue: (row) => row.owner,
                },
                {
                  header: "Action",
                  render: (row) => (
                    <div className="row-actions">
                      <button
                        type="button"
                        aria-current={row.id === selectedSourceId ? "page" : undefined}
                        onClick={() => openSource(row.id)}
                      >
                        {row.id === selectedSourceId ? "Opened" : "Open"}
                      </button>
                      <button
                        type="button"
                        onClick={() => onNavigate("/documents/:sourceId", { sourceId: row.id })}
                      >
                        View Doc
                      </button>
                      {canDeleteSources ? (
                        <button
                          type="button"
                          disabled={command.busy}
                          onClick={() =>
                            command.runCommand("Delete source", () =>
                              deleteSource(row.id, row.title)
                            )
                          }
                        >
                          Delete
                        </button>
                      ) : null}
                    </div>
                  ),
                },
              ]}
            />
          ) : (
            !sources.loading && <EmptyState label="No sources available." />
          )}
        </CollapsibleCard>
        <CollapsibleCard title="Knowledge Upload">
          <p>
            Steps: upload or link -&gt; classify -&gt; extraction settings -&gt; confirm and start.
          </p>
          <div className="wizard-steps">
            {Array.from({ length: totalWizardSteps }, (_, index) => index + 1).map((step) => (
              <button
                type="button"
                key={step}
                className={wizardStep === step ? "step active" : "step"}
                aria-current={wizardStep === step ? "step" : undefined}
                disabled={command.busy}
                onClick={() => setWizardStep(step)}
              >
                {step}
              </button>
            ))}
          </div>
          <div hidden={wizardStep !== 1}>
            <label className="field">
              <span>Upload file</span>
              <input
                key={fileInputKey}
                type="file"
                accept={acceptedUploadFormats}
                onChange={(event) => selectUploadFile(event.target.files?.[0] ?? null)}
              />
            </label>
            <TextField
              label="Canonical source URI (optional)"
              value={upload.sourceUri}
              onChange={(value) => setUpload({ ...upload, sourceUri: value })}
            />
            <TextField
              label="Source version (optional)"
              value={upload.sourceVersion}
              onChange={(value) => setUpload({ ...upload, sourceVersion: value })}
            />
            <TextField
              label="Source date (optional)"
              value={upload.sourceDate}
              onChange={(value) => setUpload({ ...upload, sourceDate: value })}
            />
            <p className="subtle">Supported file types: {supportedUploadFormatLabel}.</p>
            {upload.file ? <p className="subtle">Detected format: {upload.sourceType}.</p> : null}
            <p className="subtle">
              Source URI is stored as provenance metadata for the uploaded file.
            </p>
          </div>
          <div hidden={wizardStep !== 2}>
            <label className="field">
              <span>Domain</span>
              <select
                value={upload.domainId || "ALL"}
                onChange={(event) => setUpload({ ...upload, domainId: event.target.value })}
                disabled={Boolean(uploadDomainError)}
              >
                {uploadDomainError ? (
                  <option value="">No writable domains</option>
                ) : (
                  domainOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))
                )}
              </select>
            </label>
            <SelectField
              label="Category"
              value={upload.category}
              onChange={(value) => setUpload({ ...upload, category: value })}
              options={categoryOptions}
            />
            <SelectField
              label="Sensitivity"
              value={upload.sensitivity}
              onChange={(value) => setUpload({ ...upload, sensitivity: value })}
              options={sensitivityOptions}
            />
            <TextField
              label="Owner"
              value={upload.owner}
              onChange={(value) => setUpload({ ...upload, owner: value })}
            />
            <TextField
              label="Review owner"
              value={upload.reviewOwner}
              onChange={(value) => setUpload({ ...upload, reviewOwner: value })}
            />
            <TextField
              label="Language"
              value={upload.language}
              onChange={(value) => setUpload({ ...upload, language: value })}
            />
            <TextField
              label="Extraction priority"
              value={upload.extractionPriority}
              onChange={(value) => setUpload({ ...upload, extractionPriority: value })}
            />
          </div>
          <div hidden={wizardStep !== 3}>
            <label className="field">
              <span>Extraction settings</span>
              <select
                value={upload.extractionMode}
                onChange={(event) => setUpload({ ...upload, extractionMode: event.target.value })}
              >
                {extractionModes.map((mode) => (
                  <option key={mode}>{mode}</option>
                ))}
              </select>
            </label>
            <p className="subtle">
              Deterministic mode extracts locally without embedding indexing. Deterministic + LLM
              keeps local extraction and queues local bge-small embedding jobs for retrieval.
            </p>
          </div>
          <div hidden={wizardStep !== 4}>
            <p className="upload-review-callout">
              Review the upload payload before starting ingestion.
            </p>
            {buildUploadSummary(upload).map(([label, value]) => (
              <p key={label}>
                <strong>{label}:</strong> {value}
              </p>
            ))}
            <p>Estimated processing stages will be shown after upload.</p>
            <div className="actions-row">
              <button
                type="button"
                disabled={
                  Boolean(
                    uploadStepError(1, upload) || uploadStepError(2, upload) || uploadDomainError
                  ) || command.busy
                }
                onClick={handleUpload}
              >
                Start upload
              </button>
            </div>
          </div>
          {uploadDomainError ? (
            <p role="alert">{uploadDomainError}</p>
          ) : currentStepError ? (
            <p role="alert">{currentStepError}</p>
          ) : null}
          <div className="actions-row">
            <button
              type="button"
              disabled={wizardStep <= 1 || command.busy}
              onClick={goToPreviousStep}
            >
              Previous
            </button>
            <button
              type="button"
              disabled={
                wizardStep >= totalWizardSteps ||
                Boolean(currentStepError || (wizardStep === 2 ? uploadDomainError : null)) ||
                command.busy
              }
              onClick={goToNextStep}
            >
              Next
            </button>
            <button
              type="button"
              disabled={command.busy}
              onClick={() => {
                setUpload(defaultUploadState);
                setUploadFailure(null);
                setLastUploadSummary(null);
                setFileInputKey((current) => current + 1);
                setWizardStep(1);
              }}
            >
              Reset
            </button>
          </div>
          {command.message ? <p role="status">{command.message}</p> : null}
          {uploadFailure ? <p role="alert">Processing failed: {uploadFailure}</p> : null}
          {lastUploadSummary ? (
            <div className="upload-result-summary">
              {lastUploadSummary.map(([label, value]) => (
                <p key={label}>
                  <strong>{label}:</strong> {value}
                </p>
              ))}
            </div>
          ) : null}
        </CollapsibleCard>
        <CollapsibleCard
          ref={sourceDetailRef}
          className="source-detail-card"
          title="Source Job details"
          tabIndex={-1}
        >
          {sourceDetail.loading ? <LoadingState label="Loading source..." /> : null}
          <ErrorState error={sourceDetail.error} />
          {selectedSourceId && sourceDetail.data ? (
            <>
              <p>
                <strong>Document Title:</strong> {sourceDetail.data.title}
              </p>
              <p>
                <strong>Domain:</strong> {domainLabel(sourceDetail.data.domainId)}
                <br />
                <strong>Category:</strong> {sourceDetail.data.category || "Uncategorized"}
                <br />
                <strong>Sensitivity:</strong> {sourceDetail.data.sensitivity}
              </p>
              <div className="actions-row">
                <button
                  type="button"
                  disabled={
                    !selectedJobDetail ||
                    !["failed", "quarantined"].includes(selectedJobDetail.state) ||
                    command.busy
                  }
                  onClick={() =>
                    selectedJobDetail &&
                    confirmGovernedAction(
                      `Retry ingestion for ${selectedJobDetail.sourceName}? This action will be audited.`
                    ) &&
                    command.runCommand("Retry job", () => jobsClient.retry(selectedJobDetail.id))
                  }
                >
                  Retry
                </button>
                <button
                  type="button"
                  disabled={!selectedJobDetail || command.busy}
                  onClick={() =>
                    selectedJobDetail &&
                    confirmGovernedAction(
                      `Cancel ingestion for ${selectedJobDetail.sourceName}? This action will be audited.`
                    ) &&
                    command.runCommand("Cancel job", () => jobsClient.cancel(selectedJobDetail.id))
                  }
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={!selectedSourceId || command.busy}
                  onClick={() =>
                    confirmGovernedAction(
                      `${reprocessSourceLabel} for selected source? This action will be audited.`
                    ) && runSourceJobAction()
                  }
                >
                  {reprocessSourceLabel}
                </button>
              </div>
              <SourceHistoryPanel sourceId={selectedSourceId} />
            </>
          ) : (
            !sourceDetail.loading && <EmptyState label="Select a source to view details." />
          )}

          {selectedElementId ? (
            <section
              ref={sourceElementRef}
              className="source-element-evidence"
              tabIndex={-1}
              aria-labelledby="source-element-evidence-title"
            >
              <h3 id="source-element-evidence-title">Exact source element</h3>
              {sourceElements.loading ? (
                <LoadingState label="Loading permitted source element..." />
              ) : null}
              <ErrorState error={sourceElements.error} />
              {selectedSourceElement ? (
                <EvidenceCard
                  evidence={{
                    sourceTitle: sourceDetail.data?.title,
                    sourceType: selectedSourceElement.elementType || selectedSourceElement.kind,
                    version: selectedSourceElement.sourceVersionId,
                    pageLabel:
                      selectedSourceElement.sourcePageId ||
                      (selectedSourceElement.pageNumber === null ||
                      selectedSourceElement.pageNumber === undefined
                        ? undefined
                        : `Page ${selectedSourceElement.pageNumber}`),
                    elementLabel: selectedSourceElement.id,
                    quote: selectedSourceElement.text,
                    extractionMethod: selectedSourceElement.converterMethod || "source extraction",
                    confidence:
                      selectedSourceElement.confidence === null ||
                      selectedSourceElement.confidence === undefined
                        ? undefined
                        : `${Math.round(selectedSourceElement.confidence * 100)}%`,
                    verificationStatus: sourceDetail.data?.status,
                    sensitivity: sourceDetail.data?.sensitivity,
                    permissionsState: "Permitted by source access",
                    pageCoordinates:
                      selectedSourceElement.pageNumber === null ||
                      selectedSourceElement.pageNumber === undefined
                        ? undefined
                        : `p.${selectedSourceElement.pageNumber}`,
                  }}
                />
              ) : (
                !sourceElements.loading &&
                !sourceElements.error && (
                  <EmptyState
                    label={`Source element ${shortId(selectedElementId)} was not found in this permitted source.`}
                  />
                )
              )}
            </section>
          ) : null}

          <p className="subtle">
            Retry failed job reruns the failed job. Reprocess source creates a fresh ingestion job
            unless the latest source job failed.
          </p>

          {jobDetail.loading ? <LoadingState label="Loading job detail..." /> : null}
          <ErrorState error={jobDetail.error} />
          {selectedSourceId ? (
            <>
              <h3>Job history</h3>
              <div className="source-job-history-table">
                <DataTable
                  rows={sourceJobs}
                  emptyLabel="No jobs found for this source."
                  density="compact"
                  caption={`Jobs/retries: ${selectedSourceAttempts}`}
                  getRowKey={(row) => row.id}
                  columns={[
                    {
                      header: "Job ID",
                      render: (row) => row.id,
                      sortValue: (row) => row.id,
                    },
                    {
                      header: "Created",
                      render: (row) => formatGmtDateTime(row.createdAt),
                      sortValue: (row) => jobTimestamp(row),
                    },
                    {
                      header: "Started",
                      render: (row) => formatGmtDateTime(row.startedAt),
                      sortValue: (row) => Date.parse(row.startedAt) || 0,
                    },
                    {
                      header: "Finished",
                      render: (row) => formatGmtDateTime(row.finishedAt),
                      sortValue: (row) => Date.parse(row.finishedAt) || 0,
                    },
                    {
                      header: "Error",
                      render: (row) => row.errorMessage || "none",
                      sortValue: (row) => row.errorMessage,
                    },
                  ]}
                />
              </div>
            </>
          ) : null}
          {selectedJobDetail ? (
            <>
              {command.message ? <p role="status">{command.message}</p> : null}

              <h3>Pipeline status</h3>
              <p className="subtle">Current state: {selectedJobDetail.state}</p>
              <ul>
                {stageRows.map((row) => (
                  <li key={row.stage}>
                    {row.stage} - {row.status}
                    {row.detail ? (
                      <>
                        <div className="subtle">
                          Worker: {row.detail.worker} - Adapter: {row.detail.adapter}
                        </div>
                        <div className="subtle">
                          Start: {row.detail.startedAt || "n/a"} - End:{" "}
                          {row.detail.finishedAt || "n/a"}
                        </div>
                        {row.detail.warnings.length > 0 ? (
                          <div className="subtle">Warnings: {row.detail.warnings.join(" | ")}</div>
                        ) : null}
                        {row.detail.logs.length > 0 ? (
                          <div className="subtle">Logs: {row.detail.logs.join(" | ")}</div>
                        ) : null}
                        {Object.keys(row.detail.outputCounts).length > 0 ? (
                          <div className="subtle">
                            Output: {JSON.stringify(row.detail.outputCounts)}
                          </div>
                        ) : null}
                      </>
                    ) : (
                      <div className="subtle">
                        Worker: n/a - Adapter: n/a - Start: n/a - End: n/a
                      </div>
                    )}
                  </li>
                ))}
              </ul>
              <p className="subtle">
                Review-required, quarantined, and failed states require the next governed action
                before the source can continue.
              </p>
            </>
          ) : (
            !jobDetail.loading && (
              <EmptyState
                label={
                  selectedJobId
                    ? `Job ${shortId(selectedJobId)} was not found.`
                    : "Select a job to inspect details."
                }
              />
            )
          )}

          {extractionReport.loading ? <LoadingState label="Loading extraction report..." /> : null}
          <ErrorState error={extractionReport.error} />
          {extractionReport.data ? (
            <>
              <details className="technical-details">
                <summary>Extraction report</summary>
                <pre className="code-block">
                  {JSON.stringify(extractionReport.data.report, null, 2)}
                </pre>
              </details>
              <p>Source evidence is not verified until extracted claims are reviewed.</p>
            </>
          ) : (
            !extractionReport.loading && <EmptyState label="No extraction report available." />
          )}
          {ingestionReceipt.loading ? <LoadingState label="Loading ingestion receipt..." /> : null}
          <ErrorState error={ingestionReceipt.error} />
          {receipt && receipt.overall_status && receipt.operational && receipt.completeness ? (
            <div className="source-receipt-panel">
              <h3>Ingestion receipt — {receipt.overall_status.replace(/_/g, " ")}</h3>
              <p className="subtle">
                Operational completion, source representation, knowledge extraction, and publication
                are independent gates.
              </p>
              <p>
                Windows: {receipt.operational.processed_windows ?? 0}/
                {receipt.operational.expected_windows ?? 0}; elements attempted:{" "}
                {receipt.operational.attempted_elements ?? 0}; no claim:{" "}
                {receipt.operational.no_claim_elements ?? 0}; failed/deferred:{" "}
                {(receipt.operational.failed_elements ?? 0) +
                  (receipt.operational.deferred_elements ?? 0)}
              </p>
              <div className="source-kpis completeness-grid" aria-label="Completeness gates">
                {(["operational", "source", "extraction", "publication"] as const).map((layer) => {
                  const result = receipt.completeness[layer] ?? {};
                  return (
                    <article className="kpi-card" key={layer}>
                      <strong>{String(result.status ?? "unknown").replace(/_/g, " ")}</strong>
                      <span>{layer} completeness</span>
                      {typeof result.expected_windows === "number" &&
                      typeof result.processed_windows === "number" ? (
                        <small>
                          {result.processed_windows}/{result.expected_windows} windows
                        </small>
                      ) : null}
                      {typeof result.expected_elements === "number" &&
                      typeof result.terminal_outcomes === "number" ? (
                        <small>
                          {result.terminal_outcomes}/{result.expected_elements} element outcomes
                        </small>
                      ) : null}
                      {typeof result.expected_pages === "number" &&
                      typeof result.reconciled_pages === "number" ? (
                        <small>
                          {result.reconciled_pages}/{result.expected_pages} pages reconciled
                        </small>
                      ) : null}
                    </article>
                  );
                })}
              </div>
              <h3>Unresolved source elements</h3>
              {receipt.unresolved_elements.length > 0 ? (
                <ul>
                  {receipt.unresolved_elements.map((element) => (
                    <li key={element.id} id={element.id}>
                      <strong>{element.kind}</strong> — page {element.page ?? "n/a"};{" "}
                      {element.outcome}; {element.reason || "review required"}
                      {element.attempt
                        ? `; attempt ${shortId(element.attempt.id)} (${element.attempt.outcome})`
                        : "; no attempt recorded"}
                      {element.review_task_id ? (
                        <a
                          href={`/governance/review/${encodeURIComponent(element.review_task_id)}`}
                        >
                          {" "}
                          Open review task {shortId(element.review_task_id)}
                        </a>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : (
                <EmptyState label="No unresolved source elements." />
              )}
              <p className="subtle">
                Native elements: {receipt.observability.native_elements}; mapped:{" "}
                {receipt.observability.mapped_elements}; unsupported:{" "}
                {receipt.observability.unsupported_elements}; exact claims:{" "}
                {receipt.observability.exact_claims}; quarantined facts:{" "}
                {receipt.observability.quarantined_facts}.
              </p>
              <p className="subtle">
                Element outcomes: {JSON.stringify(receipt.observability.element_outcomes)};
                embedding gate:{" "}
                {String(receipt.observability.embedding_gate.status || "not started")}; completed:{" "}
                {String(receipt.observability.embedding_gate.completed_at || "n/a")}; semantic
                decisions: {receipt.observability.semantic_decisions.equivalent} equivalent /{" "}
                {receipt.observability.semantic_decisions.review_tasks} awaiting review.
              </p>
              <details className="technical-details">
                <summary>Technical receipt details</summary>
                <pre className="code-block">{JSON.stringify(receipt, null, 2)}</pre>
              </details>
            </div>
          ) : null}
        </CollapsibleCard>
      </div>
    </section>
  );
}
