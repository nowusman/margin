export type UploadState = {
  sourceType: string;
  file: File | null;
  sourceUri: string;
  sourceVersion: string;
  sourceDate: string;
  domainId: string;
  category: string;
  sensitivity: string;
  owner: string;
  reviewOwner: string;
  language: string;
  extractionPriority: string;
  extractionMode: string;
};

type SourceTypeOption = {
  label: string;
  acceptedFormats: string;
};

export type UploadResponse = {
  id?: string;
  source_document_id?: string;
  source_document?: {
    id?: string;
  };
};

export const sourceTypeOptions: SourceTypeOption[] = [
  { label: "PDF", acceptedFormats: ".pdf" },
  { label: "DOCX", acceptedFormats: ".docx" },
  { label: "PPTX", acceptedFormats: ".pptx" },
  {
    label: "Markdown/TXT",
    acceptedFormats: ".md,.markdown,.txt",
  },
  { label: "Structurizr DSL", acceptedFormats: ".dsl" },
  { label: "Mermaid", acceptedFormats: ".mmd,.mermaid" },
  { label: "PlantUML", acceptedFormats: ".puml,.plantuml,.iuml" },
  { label: "YAML", acceptedFormats: ".yaml,.yml" },
];

export const sensitivityOptions = [
  "public",
  "internal",
  "confidential",
  "regulated",
  "security-sensitive",
];
export const extractionModes = ["deterministic", "deterministic + LLM"];
export const totalWizardSteps = 4;

export const uniqueNonEmpty = (values: string[]): string[] =>
  Array.from(new Set(values.map((value) => value.trim()).filter(Boolean)));

export const acceptedUploadFormats = uniqueNonEmpty(
  sourceTypeOptions.flatMap((option) => option.acceptedFormats.split(","))
).join(",");

export const supportedUploadFormatLabel =
  "PDF, DOCX, PPTX, Markdown, TXT, Structurizr DSL, Mermaid, PlantUML, YAML";

export const defaultUploadState: UploadState = {
  sourceType: sourceTypeOptions[0].label,
  file: null,
  sourceUri: "",
  sourceVersion: "",
  sourceDate: "",
  domainId: "ALL",
  category: "",
  sensitivity: "internal",
  owner: "",
  reviewOwner: "",
  language: "en",
  extractionPriority: "normal",
  extractionMode: "deterministic",
};

export const detectSourceTypeOption = (filename: string): SourceTypeOption | null => {
  const normalized = filename.toLowerCase();
  return (
    sourceTypeOptions.find((option) =>
      option.acceptedFormats
        .split(",")
        .map((extension) => extension.trim().toLowerCase())
        .filter(Boolean)
        .some((extension) => normalized.endsWith(extension))
    ) ?? null
  );
};

export const normalizeUploadDomain = (domainId: string): string => {
  const normalized = domainId.trim();
  return normalized.toLowerCase() === "all domains" ? "ALL" : normalized;
};

export const buildUploadSummary = (upload: UploadState): Array<[string, string]> => {
  const domainId = normalizeUploadDomain(upload.domainId);
  return [
    ["Detected format", upload.file ? upload.sourceType : "Missing file"],
    ["Input", upload.file?.name || "Missing file"],
    ["Domain", domainId === "ALL" ? "All Domains" : domainId],
    ["Category", upload.category || "Uncategorized"],
    ["Sensitivity", upload.sensitivity || "internal"],
    ["Owner", upload.owner || "Unowned"],
    ["Review owner", upload.reviewOwner || "Unassigned"],
    ["Language", upload.language || "Unspecified"],
    ["Extraction mode", upload.extractionMode || "deterministic"],
    ["Extraction priority", upload.extractionPriority || "normal"],
    ["Source version", upload.sourceVersion || "n/a"],
    ["Source date", upload.sourceDate || "n/a"],
  ];
};

export const uploadStepError = (step: number, upload: UploadState): string | null => {
  if (step === 1) {
    if (!upload.file) return "Select a supported file before continuing.";
    if (!detectSourceTypeOption(upload.file.name)) {
      return "Select one of the supported file types before continuing.";
    }
  }
  if (step === 2) {
    if (!upload.domainId.trim()) return "Domain is required for upload classification.";
    if (!upload.category.trim()) return "Category is required for upload classification.";
    if (!upload.sensitivity.trim()) return "Sensitivity is required for upload classification.";
  }
  return null;
};
