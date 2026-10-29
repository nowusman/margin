import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type * as oidcModule from "./oidc";

let oidc: typeof oidcModule;

const storage = (): Storage => {
  const values = new Map<string, string>();
  return {
    clear: () => values.clear(),
    getItem: (key: string) => values.get(key) ?? null,
    key: (index: number) => Array.from(values.keys())[index] ?? null,
    get length() {
      return values.size;
    },
    removeItem: (key: string) => {
      values.delete(key);
    },
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
  };
};

const installBrowserGlobals = (
  url: string
): { assigned: string[]; sessionStore: Storage; localStore: Storage } => {
  const assigned: string[] = [];
  const current = new URL(url);
  const sessionStore = storage();
  const localStore = storage();
  const location = {
    get hash() {
      return current.hash;
    },
    get origin() {
      return current.origin;
    },
    get pathname() {
      return current.pathname;
    },
    get search() {
      return current.search;
    },
    assign: (value: string) => {
      assigned.push(value);
    },
  };

  vi.stubGlobal("window", {
    history: {
      replaceState: (_state: unknown, _title: string, path: string) => {
        assigned.push(`replace:${path}`);
      },
    },
    location,
  });
  vi.stubGlobal("document", { title: "Margin" });
  vi.stubGlobal("sessionStorage", sessionStore);
  vi.stubGlobal("localStorage", localStore);
  vi.stubGlobal("crypto", {
    getRandomValues: (bytes: Uint8Array) => {
      bytes.fill(7);
      return bytes;
    },
    subtle: {
      digest: () => Promise.resolve(new Uint8Array([1, 2, 3]).buffer),
    },
  });
  return { assigned, sessionStore, localStore };
};

describe("OIDC browser flow", () => {
  beforeEach(async () => {
    vi.resetModules();
    vi.stubEnv("VITE_OIDC_ISSUER", "http://localhost:8082/realms/margin");
    vi.stubEnv("VITE_OIDC_CLIENT_ID", "margin-web");
    oidc = await import("./oidc");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("starts Keycloak login with PKCE and stores the return path", async () => {
    const { assigned, sessionStore } = installBrowserGlobals(
      "http://127.0.0.1:5173/sources?tab=all#top"
    );

    await oidc.startOidcLogin();

    const redirect = new URL(assigned[0]);
    expect(redirect.origin).toBe("http://localhost:8082");
    expect(redirect.pathname).toBe("/realms/margin/protocol/openid-connect/auth");
    expect(redirect.searchParams.get("client_id")).toBe("margin-web");
    expect(redirect.searchParams.get("code_challenge_method")).toBe("S256");
    expect(redirect.searchParams.get("redirect_uri")).toBe("http://127.0.0.1:5173/sources");
    expect(sessionStore.getItem("margin_oidc_return_path")).toBe("/sources?tab=all#top");
  });

  it("exchanges callback code and restores the stored return path", async () => {
    const { assigned, localStore, sessionStore } = installBrowserGlobals(
      "http://127.0.0.1:5173/sources?code=abc&state=state-1"
    );
    sessionStore.setItem("margin_oidc_state", "state-1");
    sessionStore.setItem("margin_oidc_pkce_verifier", "verifier-1");
    sessionStore.setItem("margin_oidc_return_path", "/sources?tab=all#top");
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ access_token: "access-1", id_token: "id-1" }), {
            status: 200,
          })
        )
      )
    );

    await expect(oidc.completeOidcLogin()).resolves.toBe(true);

    expect(localStore.getItem("margin_access_token")).toBe("access-1");
    expect(localStore.getItem("margin_id_token")).toBe("id-1");
    expect(sessionStore.getItem("margin_oidc_state")).toBeNull();
    expect(assigned).toContain("replace:/sources?tab=all#top");
  });

  it("clears local auth and redirects to Keycloak logout with id token hint", () => {
    const { assigned, localStore } = installBrowserGlobals("http://127.0.0.1:5173/");
    localStore.setItem("margin_access_token", "access-1");
    localStore.setItem("margin_id_token", "id-1");

    oidc.redirectToLoggedOut();

    const redirect = new URL(assigned[0]);
    expect(redirect.pathname).toBe("/realms/margin/protocol/openid-connect/logout");
    expect(redirect.searchParams.get("client_id")).toBe("margin-web");
    expect(redirect.searchParams.get("id_token_hint")).toBe("id-1");
    expect(redirect.searchParams.get("post_logout_redirect_uri")).toBe(
      "http://127.0.0.1:5173/logged-out"
    );
    expect(localStore.getItem("margin_access_token")).toBeNull();
  });
});
