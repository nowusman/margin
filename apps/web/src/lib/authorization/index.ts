import { AppRoute, Role } from "../../features/pages/types";

type RouteAccessRule = {
  route: AppRoute;
  roles: Role[];
};

const GOVERNANCE_WRITE_ROLES: Role[] = [
  "Architect",
  "Domain Steward",
  "Governance Admin",
  "Platform Admin",
];

const ROUTE_ACCESS_RULES: RouteAccessRule[] = [
  { route: "/governance", roles: [...GOVERNANCE_WRITE_ROLES, "Auditor"] },
  { route: "/governance/records/:recordId", roles: [...GOVERNANCE_WRITE_ROLES, "Auditor"] },
  { route: "/governance/review", roles: [...GOVERNANCE_WRITE_ROLES, "Auditor"] },
  { route: "/governance/review/:taskId", roles: [...GOVERNANCE_WRITE_ROLES, "Auditor"] },
  { route: "/admin/models", roles: ["Platform Admin"] },
  { route: "/admin/access", roles: ["Platform Admin"] },
  { route: "/admin/system", roles: ["Platform Admin", "Auditor"] },
];

export const canAccessRoute = (role: Role, route: AppRoute): boolean => {
  const rule = ROUTE_ACCESS_RULES.find((item) => item.route === route);
  if (!rule) return true;
  return rule.roles.includes(role);
};

export const routeAllowedRoles = (route: AppRoute): Role[] | null => {
  const rule = ROUTE_ACCESS_RULES.find((item) => item.route === route);
  return rule ? rule.roles : null;
};

export const hasPermissionLimitBanner = (role: Role): boolean =>
  role === "Viewer" || role === "Auditor";
