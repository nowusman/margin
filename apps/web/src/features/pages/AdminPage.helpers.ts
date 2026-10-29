import type { ModelRegistryItem } from "../../lib/apiTypes";

export type AccessProbeResult = {
  user: string;
  canSee: string[];
  denied: string[];
  decisionTrace: string;
};

export type ModelFormState = {
  displayName: string;
  purpose: string;
  runtime: string;
  modelName: string;
  endpointUrl: string;
  secretReference: string;
  capabilities: string;
  contextLength: string;
  maxTokens: string;
  temperatureDefault: string;
  license: string;
  hardwareProfile: string;
  defaultScopes: string;
  systemPrompt: string;
  status: string;
};

export type ModelDialogMode = "add" | "edit";

export const toCsv = (items: string[]): string => items.join(", ");

export const parseCsv = (value: string): string[] => {
  const parsed = value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  return Array.from(new Set(parsed));
};

export const modelToForm = (model: ModelRegistryItem): ModelFormState => ({
  displayName: model.displayName,
  purpose: model.purpose,
  runtime: model.runtime,
  modelName: model.modelName,
  endpointUrl: model.endpointUrl,
  secretReference: "",
  capabilities: JSON.stringify(model.capabilities || {}, null, 2),
  contextLength: String(model.contextLength),
  maxTokens: String(model.maxTokens),
  temperatureDefault: String(model.temperatureDefault),
  license: model.license,
  hardwareProfile: model.hardwareProfile,
  defaultScopes: toCsv(model.defaultScopes),
  systemPrompt: model.systemPrompt,
  status: model.status,
});

export const emptyModelForm = (): ModelFormState => ({
  displayName: "",
  purpose: "extraction",
  runtime: "openai-compatible",
  modelName: "",
  endpointUrl: "",
  secretReference: "",
  capabilities: "{}",
  contextLength: "0",
  maxTokens: "0",
  temperatureDefault: "0",
  license: "model-specific",
  hardwareProfile: "configured-runtime",
  defaultScopes: "internal",
  systemPrompt: "",
  status: "disabled",
});

export const modelFormPayload = (
  form: ModelFormState
): Partial<ModelRegistryItem> & {
  secretReference?: string;
  capabilities?: Record<string, unknown>;
} => {
  let capabilitiesObj: Record<string, unknown> = {};
  try {
    const parsed: unknown = JSON.parse(form.capabilities || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("Capabilities must be a JSON object.");
    }
    capabilitiesObj = parsed as Record<string, unknown>;
  } catch {
    throw new Error("Capabilities must be a valid JSON object.");
  }
  const payload: Partial<ModelRegistryItem> & {
    secretReference?: string;
    capabilities?: Record<string, unknown>;
  } = {
    displayName: form.displayName,
    purpose: form.purpose as ModelRegistryItem["purpose"],
    runtime: form.runtime as ModelRegistryItem["runtime"],
    modelName: form.modelName,
    endpointUrl: form.endpointUrl,
    capabilities: capabilitiesObj,
    contextLength: Number(form.contextLength) || 0,
    maxTokens: Number(form.maxTokens) || 0,
    temperatureDefault: Number(form.temperatureDefault) || 0,
    license: form.license,
    hardwareProfile: form.hardwareProfile,
    defaultScopes: parseCsv(form.defaultScopes),
    systemPrompt: form.systemPrompt,
    status: form.status as ModelRegistryItem["status"],
  };
  if (form.secretReference.trim()) {
    payload.secretReference = form.secretReference.trim();
  }
  return payload;
};
