import React from "react";

type Tone = "neutral" | "success" | "warning" | "danger" | "info";

type BadgeProps = {
  label: string;
  tone?: Tone;
};

function toneClass(tone: Tone): string {
  switch (tone) {
    case "success":
      return "badge tone-success";
    case "warning":
      return "badge tone-warning";
    case "danger":
      return "badge tone-danger";
    case "info":
      return "badge tone-info";
    default:
      return "badge";
  }
}

function badgeToneByValue(value: string): Tone {
  const low = value.toLowerCase();
  if (["verified", "accepted", "current", "public"].includes(low)) return "success";
  if (["candidate", "stale", "review due soon", "internal", "extracted", "inferred"].includes(low))
    return "warning";
  if (["rejected", "contradicted", "expired", "restricted"].includes(low)) return "danger";
  if (["confidential", "ambiguous"].includes(low)) return "info";
  return "neutral";
}

export function StatusBadge({ label }: BadgeProps): React.JSX.Element {
  return <span className={toneClass(badgeToneByValue(label))}>{label}</span>;
}

export function ConfidenceBadge({ label }: BadgeProps): React.JSX.Element {
  return <span className={toneClass(badgeToneByValue(label))}>{label}</span>;
}

export function SensitivityBadge({ label }: BadgeProps): React.JSX.Element {
  return <span className={toneClass(badgeToneByValue(label))}>{label}</span>;
}

export function FreshnessBadge({ label }: BadgeProps): React.JSX.Element {
  return <span className={toneClass(badgeToneByValue(label))}>{label}</span>;
}
