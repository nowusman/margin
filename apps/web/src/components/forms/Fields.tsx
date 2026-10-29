import React from "react";

type FieldProps = {
  disabled?: boolean;
  label: string;
  value: string;
  onChange: (_value: string) => void;
  placeholder?: string;
  type?: "password" | "text";
};

type SelectFieldProps = FieldProps & {
  options: string[];
};

type TextAreaFieldProps = FieldProps & {
  rows?: number;
};

export function TextField({
  disabled = false,
  label,
  value,
  onChange,
  placeholder,
  type = "text",
}: FieldProps): React.JSX.Element {
  return (
    <label className="field">
      <span>{label}</span>
      <input
        disabled={disabled}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        type={type}
      />
    </label>
  );
}

export function SelectField({
  disabled = false,
  label,
  value,
  onChange,
  options,
}: SelectFieldProps): React.JSX.Element {
  return (
    <label className="field">
      <span>{label}</span>
      <select disabled={disabled} value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </label>
  );
}

export function TextAreaField({
  disabled = false,
  label,
  value,
  onChange,
  placeholder,
  rows = 4,
}: TextAreaFieldProps): React.JSX.Element {
  return (
    <label className="field">
      <span>{label}</span>
      <textarea
        disabled={disabled}
        rows={rows}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
      />
    </label>
  );
}
