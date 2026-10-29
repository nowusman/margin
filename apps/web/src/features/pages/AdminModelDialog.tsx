import React from "react";
import { SelectField, TextAreaField, TextField } from "../../components/forms";
import type { ModelRegistryItem } from "../../lib/apiTypes";
import type { ModelDialogMode, ModelFormState } from "./AdminPage.helpers";

type AdminModelDialogProps = {
  busy: boolean;
  configuredSecret: boolean;
  form: ModelFormState;
  mode: ModelDialogMode;
  onCancel: () => void;
  onChange: (_key: keyof ModelFormState, _value: string) => void;
  onSave: () => void;
};

export function AdminModelDialog({
  busy,
  configuredSecret,
  form,
  mode,
  onCancel,
  onChange,
  onSave,
}: AdminModelDialogProps): React.JSX.Element {
  return (
    <div className="modal-backdrop">
      <div
        className="modal-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="model-dialog-title"
      >
        <div className="modal-header">
          <h3 id="model-dialog-title">
            {mode === "add" ? "Add model configuration" : "Edit model configuration"}
          </h3>
          <button type="button" onClick={onCancel}>
            Close
          </button>
        </div>
        <TextField
          label="Display name"
          value={form.displayName}
          onChange={(value) => onChange("displayName", value)}
        />
        <SelectField
          label="Purpose"
          value={form.purpose}
          onChange={(value) => onChange("purpose", value)}
          options={["extraction", "vision_ocr", "answer_synthesis", "semantic_resolution"]}
        />
        <TextField
          label="Runtime"
          value={form.runtime}
          onChange={(value) => onChange("runtime", value)}
        />
        <TextField
          label="Model name"
          value={form.modelName}
          onChange={(value) => onChange("modelName", value)}
        />
        <TextField
          label="Endpoint URL"
          value={form.endpointUrl}
          onChange={(value) => onChange("endpointUrl", value)}
        />
        <TextField
          label={
            mode === "edit" && configuredSecret
              ? "Secret reference (configured)"
              : "Secret reference (e.g. env:KEY or key)"
          }
          value={form.secretReference}
          onChange={(value) => onChange("secretReference", value)}
          type="password"
        />
        <TextAreaField
          label="Capabilities (JSON)"
          value={form.capabilities}
          onChange={(value) => onChange("capabilities", value)}
          rows={3}
        />
        <TextField
          label="Context length"
          value={form.contextLength}
          onChange={(value) => onChange("contextLength", value)}
        />
        <TextField
          label="Max tokens"
          value={form.maxTokens}
          onChange={(value) => onChange("maxTokens", value)}
        />
        <TextField
          label="Temperature default"
          value={form.temperatureDefault}
          onChange={(value) => onChange("temperatureDefault", value)}
        />
        <TextField
          label="License"
          value={form.license}
          onChange={(value) => onChange("license", value)}
        />
        <TextField
          label="Hardware profile"
          value={form.hardwareProfile}
          onChange={(value) => onChange("hardwareProfile", value)}
        />
        <TextField
          label="Default scopes (comma separated)"
          value={form.defaultScopes}
          onChange={(value) => onChange("defaultScopes", value)}
        />
        <TextAreaField
          label="System prompt"
          value={form.systemPrompt}
          onChange={(value) => onChange("systemPrompt", value)}
          rows={8}
        />
        <SelectField
          label="Status"
          value={form.status}
          onChange={(value) => onChange("status", value)}
          options={["active", "disabled", "experimental"] satisfies ModelRegistryItem["status"][]}
        />
        <div className="actions-row">
          <button type="button" disabled={busy} onClick={onSave}>
            {mode === "add" ? "Add model" : "Save model settings"}
          </button>
          <button type="button" disabled={busy} onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
