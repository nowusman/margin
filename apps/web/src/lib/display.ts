const idLikePattern =
  /^(?:[a-z]+[-_])?[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$|^(?:src|sec|span|job|ent|adr|ib|rel|risk|waiver|debt|eval|trace|audit|conn|model|lint|rv)[-_][\w-]+$|^(?:adr|ib|risk|waiver|debt|trace):[\w:-]+$/i;

export const isTechnicalId = (value: string | undefined | null): boolean =>
  Boolean(value && idLikePattern.test(value.trim()));

export const fallbackName = (value: string | undefined | null, fallback: string): string => {
  const text = value?.trim();
  if (!text) return fallback;
  if (isTechnicalId(text)) return fallback;
  return text;
};

export const shortId = (value: string | undefined | null): string => {
  const text = value?.trim();
  if (!text) return "Unavailable";
  if (text.length <= 12) return text;
  return `${text.slice(0, 8)}...${text.slice(-4)}`;
};

export const technicalIdLabel = (value: string | undefined | null): string =>
  value?.trim() ? `Technical ID: ${value.trim()}` : "Technical ID unavailable";

export const slugFromTitle = (title: string, prefix: string): string => {
  const slug = title
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 72);
  return slug ? `${prefix}-${slug}` : `${prefix}-draft`;
};

export const relationLabel = (
  sourceName: string | undefined,
  relationType: string | undefined,
  targetName: string | undefined
): string =>
  [
    sourceName || "Unknown source",
    relationType || "relates to",
    targetName || "Unknown target",
  ].join(" ");

export const confirmGovernedAction = (summary: string): boolean => {
  if (typeof globalThis.confirm !== "function") return true;
  return globalThis.confirm(summary);
};
