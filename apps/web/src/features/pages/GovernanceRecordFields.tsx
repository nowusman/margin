import React from "react";
import { SelectField, TextAreaField, TextField } from "../../components/forms";
import type { GovernanceRecordType } from "../../lib/apiTypes";
import {
  attributeFields,
  GOVERNANCE_RECORD_TYPES,
  GovernanceFormValues,
  typeLabel,
} from "./GovernancePage.helpers";

type GovernanceRecordFieldsProps = {
  values: GovernanceFormValues;
  errors: Record<string, string>;
  disabled?: boolean;
  includeIdentity?: boolean;
  targetStatus?: string;
  onChange: (_values: GovernanceFormValues) => void;
};

const textValue = (value: unknown): string =>
  typeof value === "string" ? value : value == null ? "" : String(value);

function FieldError({ error }: { error?: string }): React.JSX.Element | null {
  return error ? (
    <span className="field-warning" role="alert">
      {error}
    </span>
  ) : null;
}

function DateField({
  label,
  value,
  error,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  error?: string;
  disabled?: boolean;
  onChange: (_value: string) => void;
}): React.JSX.Element {
  const id = `governance-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <label className="field" htmlFor={id}>
      <span>{label}</span>
      <input
        id={id}
        type="date"
        value={value}
        disabled={disabled}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? `${id}-error` : undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      {error ? (
        <span id={`${id}-error`} className="field-warning" role="alert">
          {error}
        </span>
      ) : null}
    </label>
  );
}

export function GovernanceRecordFields({
  values,
  errors,
  disabled = false,
  includeIdentity = false,
  targetStatus,
  onChange,
}: GovernanceRecordFieldsProps): React.JSX.Element {
  const update = (key: keyof GovernanceFormValues, value: string): void => {
    onChange({ ...values, [key]: value });
  };
  const updateAttributes = (key: string, value: string): void => {
    onChange({ ...values, attributes: { ...values.attributes, [key]: value } });
  };
  const selectedType = values.recordType as GovernanceRecordType;

  return (
    <>
      {includeIdentity ? (
        <div className="filter-grid">
          <div>
            <TextField
              disabled={disabled}
              label="Business key"
              value={values.businessKey}
              onChange={(value) => update("businessKey", value)}
            />
            <FieldError error={errors.businessKey} />
          </div>
          <SelectField
            disabled={disabled}
            label="Record type"
            value={selectedType}
            onChange={(recordType) =>
              onChange({
                ...values,
                recordType: recordType as GovernanceRecordType,
                attributes: {},
              })
            }
            options={GOVERNANCE_RECORD_TYPES}
          />
          <div>
            <TextField
              disabled={disabled}
              label="Domain ID"
              value={values.domainId}
              onChange={(value) => update("domainId", value)}
            />
            <FieldError error={errors.domainId} />
          </div>
        </div>
      ) : null}

      <div>
        <TextField
          disabled={disabled}
          label="Title"
          value={values.title}
          onChange={(value) => update("title", value)}
        />
        <FieldError error={errors.title} />
      </div>

      <div className="filter-grid">
        <TextField
          disabled={disabled}
          label="Owner ID"
          value={values.ownerId}
          onChange={(value) => update("ownerId", value)}
        />
        <TextField
          disabled={disabled}
          label="Steward ID"
          value={values.stewardId}
          onChange={(value) => update("stewardId", value)}
        />
        <div>
          <TextField
            disabled={disabled}
            label="Priority"
            value={values.priority}
            onChange={(value) => update("priority", value)}
          />
          <FieldError error={errors.priority} />
        </div>
        <div>
          <TextField
            disabled={disabled}
            label="Severity"
            value={values.severity}
            onChange={(value) => update("severity", value)}
          />
          <FieldError error={errors.severity} />
        </div>
      </div>

      <div className="filter-grid">
        <DateField
          label="Effective date"
          value={values.effectiveDate}
          error={errors.effectiveDate}
          disabled={disabled}
          onChange={(value) => update("effectiveDate", value)}
        />
        <DateField
          label="Review date"
          value={values.reviewDate}
          error={errors.reviewDate}
          disabled={disabled}
          onChange={(value) => update("reviewDate", value)}
        />
        <DateField
          label="Expiry date"
          value={values.expiryDate}
          error={errors.expiryDate}
          disabled={disabled}
          onChange={(value) => update("expiryDate", value)}
        />
        <DateField
          label="Target date"
          value={values.targetDate}
          error={errors.targetDate}
          disabled={disabled}
          onChange={(value) => update("targetDate", value)}
        />
      </div>

      <div className="filter-grid">
        <TextField
          disabled={disabled}
          label="External issue ID"
          value={values.externalIssueId}
          onChange={(value) => update("externalIssueId", value)}
        />
        <TextField
          disabled={disabled}
          label="External issue URL"
          value={values.externalIssueUrl}
          onChange={(value) => update("externalIssueUrl", value)}
        />
      </div>

      <section aria-labelledby="governance-type-fields-title">
        <h3 id="governance-type-fields-title">{typeLabel(selectedType)} details</h3>
        <div className="filter-grid">
          {attributeFields(selectedType, targetStatus).map((field) => (
            <div key={field.key}>
              {field.multiline ? (
                <TextAreaField
                  disabled={disabled}
                  label={field.label}
                  value={textValue(values.attributes[field.key])}
                  onChange={(value) => updateAttributes(field.key, value)}
                  rows={3}
                />
              ) : (
                <TextField
                  disabled={disabled}
                  label={field.label}
                  value={textValue(values.attributes[field.key])}
                  onChange={(value) => updateAttributes(field.key, value)}
                />
              )}
              <FieldError error={errors[`attributes.${field.key}`]} />
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
