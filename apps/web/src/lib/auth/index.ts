import { useEffect, useState } from "react";
import { Role } from "../../features/pages/types";

const ACCESS_TOKEN_KEY = "margin_access_token";
const USER_KEY = "margin_user";
const GROUPS_KEY = "margin_groups";
export const AUTH_SESSION_UPDATED_EVENT = "margin-auth-updated";

const ROLE_ORDER: Role[] = [
  "Platform Admin",
  "Governance Admin",
  "Domain Steward",
  "Architect",
  "Auditor",
  "Viewer",
];

const ROLE_BY_GROUP: Record<string, Role> = {
  viewer: "Viewer",
  architect: "Architect",
  "architecture-reviewers": "Auditor",
  architecture_reviewers: "Auditor",
  "architecture-admins": "Governance Admin",
  architecture_admins: "Governance Admin",
  "margin-admin": "Platform Admin",
  margin_admin: "Platform Admin",
  "domain-steward": "Domain Steward",
  domain_steward: "Domain Steward",
  "governance-admin": "Governance Admin",
  governance_admin: "Governance Admin",
  "platform-admin": "Platform Admin",
  platform_admin: "Platform Admin",
  auditor: "Auditor",
};

type JwtPayload = {
  preferred_username?: string;
  sub?: string;
  groups?: string[];
  realm_access?: { roles?: string[] };
};

export type AuthSession = {
  userId: string;
  role: Role;
  groups: string[];
  accessToken: string | null;
  oidcIssuer: string;
  oidcClientId: string;
  isAuthenticated: boolean;
};

const readLocalStorage = (key: string): string | null => {
  if (typeof globalThis.localStorage === "undefined") return null;
  try {
    return globalThis.localStorage.getItem(key);
  } catch {
    return null;
  }
};

const decodeBase64Url = (value: string): string => {
  const base64 = value.replace(/-/g, "+").replace(/_/g, "/");
  const pad = base64.length % 4;
  const padded = pad === 0 ? base64 : `${base64}${"=".repeat(4 - pad)}`;
  if (typeof globalThis.atob !== "function") return "";
  return globalThis.atob(padded);
};

const parseJwtPayload = (token: string | null): JwtPayload | null => {
  if (!token) return null;
  const parts = token.split(".");
  if (parts.length < 2) return null;
  try {
    return JSON.parse(decodeBase64Url(parts[1])) as JwtPayload;
  } catch {
    return null;
  }
};

const normalizeGroup = (value: string): string => {
  const lowercase = value.trim().toLowerCase();
  if (!lowercase) return "";
  const segment = lowercase.split("/").pop() ?? lowercase;
  return segment.replace(/\s+/g, "-");
};

const mergeGroups = (tokenGroups: string[], headerGroups: string[]): string[] => {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const group of [...tokenGroups, ...headerGroups]) {
    const normalized = normalizeGroup(group);
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    result.push(normalized);
  }
  return result;
};

const resolveRoleFromGroups = (groups: string[]): Role => {
  const roles = groups
    .map((group) => ROLE_BY_GROUP[group])
    .filter((role): role is Role => Boolean(role));
  for (const role of ROLE_ORDER) {
    if (roles.includes(role)) return role;
  }
  return "Viewer";
};

const env = (): Record<string, string | undefined> =>
  (import.meta as unknown as { env?: Record<string, string | undefined> }).env ?? {};

export const getAuthSession = (): AuthSession => {
  const accessToken = readLocalStorage(ACCESS_TOKEN_KEY);
  const payload = parseJwtPayload(accessToken);
  const isAuthenticated = Boolean(accessToken);
  const explicitGroups = isAuthenticated
    ? (readLocalStorage(GROUPS_KEY) ?? "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean)
    : [];
  const tokenGroups = [...(payload?.groups ?? []), ...(payload?.realm_access?.roles ?? [])];
  const groups = mergeGroups(tokenGroups, explicitGroups);
  const userId = isAuthenticated
    ? (payload?.preferred_username ?? payload?.sub ?? readLocalStorage(USER_KEY) ?? "anonymous")
    : "anonymous";
  const runtimeEnv = env();
  return {
    userId,
    role: resolveRoleFromGroups(groups),
    groups,
    accessToken,
    oidcIssuer: runtimeEnv.VITE_OIDC_ISSUER ?? "",
    oidcClientId: runtimeEnv.VITE_OIDC_CLIENT_ID ?? "",
    isAuthenticated,
  };
};

export const useAuthSession = (): AuthSession => {
  const [session, setSession] = useState<AuthSession>(() => getAuthSession());

  useEffect(() => {
    if (typeof globalThis.addEventListener !== "function") return;
    const refresh = (): void => setSession(getAuthSession());
    globalThis.addEventListener("storage", refresh);
    globalThis.addEventListener(AUTH_SESSION_UPDATED_EVENT, refresh);
    return () => {
      globalThis.removeEventListener("storage", refresh);
      globalThis.removeEventListener(AUTH_SESSION_UPDATED_EVENT, refresh);
    };
  }, []);

  return session;
};
