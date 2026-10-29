import type {
  GovernanceRecord,
  GovernanceRecordCreateInput,
  GovernanceRecordType,
  GovernanceRecordUpdateInput,
} from "../../lib/apiTypes";

export const GOVERNANCE_RECORD_TYPES: GovernanceRecordType[] = [
  "adr",
  "risk",
  "waiver",
  "technical_debt",
  "architecture_backlog",
];

export const typeLabel = (recordType: GovernanceRecordType): string =>
  ({
    adr: "ADR",
    risk: "Risk",
    waiver: "Waiver",
    technical_debt: "Technical debt",
    architecture_backlog: "Architecture Backlog",
  })[recordType];

export const lifecycleOptions: Record<GovernanceRecordType, string[]> = {
  adr: ["proposed", "accepted", "superseded", "deprecated", "rejected"],
  risk: ["identified", "mitigating", "accepted", "closed"],
  waiver: ["proposed", "approved", "expired", "revoked", "rejected"],
  technical_debt: ["identified", "accepted", "planned", "in_progress", "resolved"],
  architecture_backlog: ["open", "planned", "in_progress", "blocked", "completed", "cancelled"],
};

export const transitionTargets = (record: GovernanceRecord): string[] => {
  const states = lifecycleOptions[record.recordType];
  const position = states.indexOf(record.lifecycleStatus);
  if (position < 0) return [];
  const transitionMap: Record<GovernanceRecordType, Record<string, string[]>> = {
    adr: {
      proposed: ["accepted", "rejected"],
      accepted: ["superseded", "deprecated"],
      superseded: [],
      deprecated: [],
      rejected: [],
    },
    risk: {
      identified: ["mitigating", "accepted", "closed"],
      mitigating: ["accepted", "closed"],
      accepted: ["mitigating", "closed"],
      closed: [],
    },
    waiver: {
      proposed: ["approved", "rejected"],
      approved: ["expired", "revoked"],
      expired: [],
      revoked: [],
      rejected: [],
    },
    technical_debt: {
      identified: ["accepted", "planned"],
      accepted: ["planned", "resolved"],
      planned: ["in_progress", "resolved"],
      in_progress: ["resolved"],
      resolved: [],
    },
    architecture_backlog: {
      open: ["planned", "cancelled"],
      planned: ["in_progress", "blocked", "cancelled"],
      in_progress: ["blocked", "completed", "planned", "cancelled"],
      blocked: ["planned", "in_progress", "cancelled"],
      completed: [],
      cancelled: [],
    },
  };
  return transitionMap[record.recordType][record.lifecycleStatus] ?? [];
};

export type GovernanceAttributeField = {
  key: string;
  label: string;
  multiline?: boolean;
  required?: boolean;
};

const sharedTransitionFields: Record<GovernanceRecordType, GovernanceAttributeField[]> = {
  adr: [],
  risk: [],
  waiver: [],
  technical_debt: [{ key: "resolution_note", label: "Resolution note", multiline: true }],
  architecture_backlog: [{ key: "completion_note", label: "Completion note", multiline: true }],
};

const requiredAttributes: Record<GovernanceRecordType, GovernanceAttributeField[]> = {
  adr: [
    { key: "context", label: "Context", multiline: true, required: true },
    { key: "decision", label: "Decision", multiline: true, required: true },
    { key: "rationale", label: "Rationale", multiline: true, required: true },
    { key: "consequences", label: "Consequences", multiline: true, required: true },
  ],
  risk: [
    { key: "likelihood", label: "Likelihood", required: true },
    { key: "impact", label: "Impact", multiline: true, required: true },
    { key: "severity", label: "Severity", required: true },
    { key: "treatment", label: "Treatment", multiline: true, required: true },
  ],
  waiver: [
    { key: "standard_or_control", label: "Standard or control", required: true },
    { key: "scope", label: "Scope", multiline: true, required: true },
    { key: "approver", label: "Approver", required: true },
    { key: "reason", label: "Reason", multiline: true, required: true },
    { key: "compensating_control", label: "Compensating control", multiline: true, required: true },
  ],
  technical_debt: [
    { key: "category", label: "Category", required: true },
    { key: "remediation", label: "Remediation", multiline: true, required: true },
    { key: "effort", label: "Effort", required: true },
    { key: "interest_or_impact", label: "Interest or impact", multiline: true, required: true },
  ],
  architecture_backlog: [
    { key: "pending_correction", label: "Pending correction", multiline: true, required: true },
    { key: "originating_governance_record_id", label: "Originating governance record ID" },
    { key: "manual_origin", label: "Manual origin", multiline: true },
  ],
};

export const attributeFields = (
  recordType: GovernanceRecordType,
  targetStatus?: string
): GovernanceAttributeField[] => {
  const fields = [...requiredAttributes[recordType]];
  if (targetStatus === "resolved" || targetStatus === "completed") {
    fields.push(
      ...sharedTransitionFields[recordType].map((field) => ({ ...field, required: true }))
    );
  }
  return fields;
};

export type GovernanceFormValues = {
  businessKey: string;
  recordType: GovernanceRecordType;
  title: string;
  domainId: string;
  ownerId: string;
  stewardId: string;
  effectiveDate: string;
  reviewDate: string;
  expiryDate: string;
  targetDate: string;
  priority: string;
  severity: string;
  externalIssueId: string;
  externalIssueUrl: string;
  attributes: Record<string, unknown>;
};

export const blankGovernanceForm = (
  recordType: GovernanceRecordType = "adr"
): GovernanceFormValues => ({
  businessKey: "",
  recordType,
  title: "",
  domainId: "",
  ownerId: "",
  stewardId: "",
  effectiveDate: "",
  reviewDate: "",
  expiryDate: "",
  targetDate: "",
  priority: "",
  severity: "",
  externalIssueId: "",
  externalIssueUrl: "",
  attributes: {},
});

export const governanceFormFromRecord = (record: GovernanceRecord): GovernanceFormValues => ({
  businessKey: record.businessKey,
  recordType: record.recordType,
  title: record.title,
  domainId: record.domainId,
  ownerId: record.ownerId ?? "",
  stewardId: record.stewardId ?? "",
  effectiveDate: record.effectiveDate ?? "",
  reviewDate: record.reviewDate ?? "",
  expiryDate: record.expiryDate ?? "",
  targetDate: record.targetDate ?? "",
  priority: record.priority ?? "",
  severity: record.severity === null ? "" : String(record.severity),
  externalIssueId: record.externalIssueId ?? "",
  externalIssueUrl: record.externalIssueUrl ?? "",
  attributes: { ...record.attributes },
});

const optional = (value: string): string | null => value.trim() || null;

const normalizedAttributes = (values: GovernanceFormValues): Record<string, unknown> => {
  const attributes = { ...values.attributes };
  if (values.recordType === "waiver") attributes.expiry_date = optional(values.expiryDate);
  return attributes;
};

const commonInput = (values: GovernanceFormValues) => ({
  title: values.title.trim(),
  attributes: normalizedAttributes(values),
  ownerId: optional(values.ownerId),
  stewardId: optional(values.stewardId),
  effectiveDate: optional(values.effectiveDate),
  reviewDate: optional(values.reviewDate),
  expiryDate: optional(values.expiryDate),
  targetDate: optional(values.targetDate),
  priority: optional(values.priority),
  severity: values.severity.trim() ? Number(values.severity) : null,
  externalIssueId: optional(values.externalIssueId),
  externalIssueUrl: optional(values.externalIssueUrl),
});

export const governanceCreateInput = (
  values: GovernanceFormValues
): GovernanceRecordCreateInput => ({
  businessKey: values.businessKey.trim(),
  recordType: values.recordType,
  domainId: values.domainId.trim(),
  ...commonInput(values),
});

export const governanceUpdateInput = (
  values: GovernanceFormValues,
  expectedRevisionId: string,
  reason: string
): GovernanceRecordUpdateInput => ({
  ...commonInput(values),
  reason: reason.trim(),
  expectedRevisionId,
});

export type GovernanceListFilters = {
  recordType: "" | GovernanceRecordType;
  lifecycleStatus: string;
  ownerId: string;
  domainId: string;
  externalIssueId: string;
  reviewBefore: string;
  expiryBefore: string;
  targetBefore: string;
  focusedView:
    | "all"
    | "decisions"
    | "open_risks"
    | "expiring_waivers"
    | "unresolved_debt"
    | "backlog";
};

export const blankGovernanceFilters = (): GovernanceListFilters => ({
  recordType: "",
  lifecycleStatus: "",
  ownerId: "",
  domainId: "",
  externalIssueId: "",
  reviewBefore: "",
  expiryBefore: "",
  targetBefore: "",
  focusedView: "all",
});

const filterKeys = [
  "recordType",
  "lifecycleStatus",
  "ownerId",
  "domainId",
  "externalIssueId",
  "reviewBefore",
  "expiryBefore",
  "targetBefore",
  "focusedView",
] as const;

export const filtersFromLocation = (): GovernanceListFilters => {
  const filters = blankGovernanceFilters();
  if (typeof globalThis.location === "undefined") return filters;
  const query = new URLSearchParams(globalThis.location.search);
  filterKeys.forEach((key) => {
    const value = query.get(key);
    if (value) filters[key] = value as never;
  });
  return filters;
};

export const saveFiltersToLocation = (filters: GovernanceListFilters): void => {
  if (
    typeof globalThis.history?.replaceState !== "function" ||
    typeof globalThis.location === "undefined"
  )
    return;
  const query = new URLSearchParams();
  filterKeys.forEach((key) => {
    const value = filters[key];
    if (value && value !== "all") query.set(key, value);
  });
  const suffix = query.toString();
  globalThis.history.replaceState(
    {},
    "",
    `${globalThis.location.pathname}${suffix ? `?${suffix}` : ""}`
  );
};

const onOrBefore = (value: string | null, latest: string): boolean =>
  !latest || Boolean(value && value.slice(0, 10) <= latest);

export const matchesListFilters = (
  record: GovernanceRecord,
  filters: GovernanceListFilters
): boolean => {
  if (!onOrBefore(record.reviewDate, filters.reviewBefore)) return false;
  if (!onOrBefore(record.expiryDate, filters.expiryBefore)) return false;
  if (!onOrBefore(record.targetDate, filters.targetBefore)) return false;
  switch (filters.focusedView) {
    case "decisions":
      return record.recordType === "adr";
    case "open_risks":
      return record.recordType === "risk" && record.lifecycleStatus !== "closed";
    case "expiring_waivers":
      return (
        record.recordType === "waiver" &&
        record.lifecycleStatus === "approved" &&
        Boolean(record.expiryDate)
      );
    case "unresolved_debt":
      return record.recordType === "technical_debt" && record.lifecycleStatus !== "resolved";
    case "backlog":
      return record.recordType === "architecture_backlog";
    default:
      return true;
  }
};

export const validateGovernanceForm = (
  values: GovernanceFormValues,
  targetStatus?: string
): Record<string, string> => {
  const errors: Record<string, string> = {};
  if (!values.title.trim()) errors.title = "Title is required.";
  if (!values.businessKey.trim()) errors.businessKey = "Business key is required.";
  if (!values.domainId.trim()) errors.domainId = "Domain is required.";
  if (
    values.severity.trim() &&
    (!Number.isInteger(Number(values.severity)) || Number(values.severity) < 0)
  ) {
    errors.severity = "Severity must be a non-negative whole number.";
  }
  if (values.effectiveDate && values.reviewDate && values.reviewDate < values.effectiveDate) {
    errors.reviewDate = "Review date cannot precede the effective date.";
  }
  if (values.effectiveDate && values.expiryDate && values.expiryDate < values.effectiveDate) {
    errors.expiryDate = "Expiry date cannot precede the effective date.";
  }
  if (values.effectiveDate && values.targetDate && values.targetDate < values.effectiveDate) {
    errors.targetDate = "Target date cannot precede the effective date.";
  }
  attributeFields(values.recordType, targetStatus).forEach((field) => {
    const value = values.attributes[field.key];
    if (field.required && (!value || (typeof value === "string" && !value.trim()))) {
      errors[`attributes.${field.key}`] = `${field.label} is required.`;
    }
  });
  if (values.recordType === "waiver" && !values.expiryDate) {
    errors.expiryDate = "Waivers require an expiry date.";
  }
  if (values.recordType === "architecture_backlog" && !values.priority.trim()) {
    errors.priority = "Architecture Backlog records require a priority.";
  }
  if (values.recordType === "architecture_backlog" && !values.targetDate) {
    errors.targetDate = "Architecture Backlog records require a target date.";
  }
  if (
    values.recordType === "architecture_backlog" &&
    !String(values.attributes.originating_governance_record_id ?? "").trim() &&
    !String(values.attributes.manual_origin ?? "").trim()
  ) {
    errors["attributes.manual_origin"] =
      "Provide an originating record ID or a documented manual origin.";
  }
  return errors;
};

export const fieldErrorsFromMessage = (message: string): Record<string, string> => {
  const errors: Record<string, string> = {};
  if (/architecture_backlog requires priority/i.test(message)) errors.priority = message;
  if (/architecture_backlog requires target_date/i.test(message)) errors.targetDate = message;
  const required = message.match(/missing required fields:\s*(.+)$/i)?.[1];
  if (!required) return { form: message };
  required
    .split(",")
    .map((field) => field.trim())
    .forEach((field) => {
      const key =
        field === "expiry_date"
          ? "expiryDate"
          : field.startsWith("originating_")
            ? "attributes.manual_origin"
            : `attributes.${field}`;
      errors[key] = message;
    });
  return errors;
};
