import { describe, expect, it } from "vitest";
import { canAccessRoute, hasPermissionLimitBanner, routeAllowedRoles } from "./index";

describe("authorization helpers", () => {
  it("allows auditor read access to the review queue", () => {
    expect(canAccessRoute("Auditor", "/governance/review")).toBe(true);
  });

  it("returns role constraints for protected routes", () => {
    expect(routeAllowedRoles("/admin/system")).toEqual(["Platform Admin", "Auditor"]);
    expect(routeAllowedRoles("/")).toBeNull();
  });

  it("flags roles that should show permission-limited banner", () => {
    expect(hasPermissionLimitBanner("Viewer")).toBe(true);
    expect(hasPermissionLimitBanner("Auditor")).toBe(true);
    expect(hasPermissionLimitBanner("Architect")).toBe(false);
  });
});
