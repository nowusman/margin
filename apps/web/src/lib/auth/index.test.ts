import { describe, expect, it } from "vitest";
import { getAuthSession } from "./index";

const setLocalStorage = (values: Record<string, string>): void => {
  const store = new Map<string, string>(Object.entries(values));
  Object.defineProperty(globalThis, "localStorage", {
    value: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
      removeItem: (key: string) => {
        store.delete(key);
      },
    },
    configurable: true,
    writable: true,
  });
};

const base64Url = (value: string): string =>
  globalThis.btoa(value).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");

const tokenWithPayload = (payload: Record<string, unknown>): string => {
  const header = base64Url(JSON.stringify({ alg: "none", typ: "JWT" }));
  const body = base64Url(JSON.stringify(payload));
  return `${header}.${body}.`;
};

describe("auth session resolution", () => {
  it("treats missing auth data as an unauthenticated viewer", () => {
    setLocalStorage({});
    const session = getAuthSession();
    expect(session.userId).toBe("anonymous");
    expect(session.role).toBe("Viewer");
    expect(session.isAuthenticated).toBe(false);
  });

  it("ignores stale local groups when the access token is missing", () => {
    setLocalStorage({ margin_user: "bob", margin_groups: "auditor" });
    const session = getAuthSession();
    expect(session.userId).toBe("anonymous");
    expect(session.role).toBe("Viewer");
    expect(session.isAuthenticated).toBe(false);
  });

  it("maps dedicated margin-admin group to platform admin role when a token exists", () => {
    setLocalStorage({
      margin_access_token: "header.payload.signature",
      margin_user: "carol",
      margin_groups: "margin-admin",
    });
    const session = getAuthSession();
    expect(session.role).toBe("Platform Admin");
    expect(session.groups).toContain("margin-admin");
    expect(session.isAuthenticated).toBe(true);
  });

  it("prefers token claims for user and role resolution", () => {
    const token = tokenWithPayload({
      preferred_username: "platform-admin-user",
      realm_access: { roles: ["platform_admin"] },
      groups: ["/teams/architecture"],
    });
    setLocalStorage({ margin_access_token: token, margin_groups: "viewer" });
    const session = getAuthSession();
    expect(session.userId).toBe("platform-admin-user");
    expect(session.role).toBe("Platform Admin");
    expect(session.groups).toContain("platform_admin");
    expect(session.isAuthenticated).toBe(true);
  });
});
