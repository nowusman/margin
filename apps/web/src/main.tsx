import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import "./styles.css";
import { AppShell } from "./components/layout/AppShell";
import { AdminPage } from "./features/pages/AdminPage";
import { AgentPage } from "./features/pages/AgentPage";
import { ArchitectureWikiPage } from "./features/pages/ArchitectureWikiPage";
import { ArchitectureViewsPage } from "./features/pages/ArchitectureViewsPage";
import { AskPage } from "./features/pages/AskPage";
import { DomainCharterPage } from "./features/pages/DomainCharterPage";
import { EntityCatalogPage } from "./features/pages/EntityCatalogPage";
import { EntityDetailPage } from "./features/pages/EntityDetailPage";
import { GovernancePage } from "./features/pages/GovernancePage";
import { GraphPage } from "./features/pages/GraphPage";
import { HomePage } from "./features/pages/HomePage";
import { KnowledgePage } from "./features/pages/KnowledgePage";
import { PersonalPage } from "./features/pages/PersonalPage";
import { QualityPage } from "./features/pages/QualityPage";
import { SourcesPage } from "./features/pages/SourcesPage";
import { AppRoute } from "./features/pages/types";
import { ViewDocumentsPage } from "./features/pages/ViewDocumentsPage";
import { AUTH_SESSION_UPDATED_EVENT, useAuthSession } from "./lib/auth";
import {
  clearAuthSession,
  completeOidcLogin,
  hasOidcConfig,
  redirectToLoggedOut,
  startOidcLogin,
} from "./lib/auth/oidc";
import { canAccessRoute, hasPermissionLimitBanner, routeAllowedRoles } from "./lib/authorization";
import { useRouter } from "./lib/routes";

export type AppProps = { initialRoute?: AppRoute };
const DOMAIN_SCOPE_KEY = "margin_domain_scope";

const runtimeEnv = (): Record<string, string | undefined> =>
  (import.meta as unknown as { env?: Record<string, string | undefined> }).env ?? {};

const isTestRuntime = (): boolean => {
  const env = runtimeEnv();
  return Boolean(env.MODE === "test" || env.VITEST === "true");
};

const isAuthAutoRedirectEnabled = (): boolean => {
  if (isTestRuntime()) return false;
  const configured = runtimeEnv().VITE_AUTH_AUTO_REDIRECT;
  if (!configured) return true;
  return !["0", "false", "no", "off"].includes(configured.trim().toLowerCase());
};

const redirectToLogin = (redirectPath?: string): void => {
  if (typeof globalThis.window === "undefined") return;
  try {
    void startOidcLogin(redirectPath);
  } catch {
    // No-op: if location navigation is blocked in tests, keep runtime stable.
  }
};

const asString = (value: unknown): string => (typeof value === "string" ? value.trim() : "");

const asStringArray = (value: unknown): string[] =>
  Array.isArray(value) ? value.map((item) => asString(item)).filter(Boolean) : [];

const applyRbacSession = (payload: unknown): void => {
  if (
    typeof globalThis.localStorage === "undefined" ||
    typeof globalThis.dispatchEvent !== "function"
  )
    return;
  const record = (payload ?? {}) as Record<string, unknown>;
  const groups = asStringArray(record.group_ids);
  const userId = asString(record.principal_id);
  if (userId) {
    globalThis.localStorage.setItem("margin_user", userId);
  }
  if (groups.length > 0) {
    globalThis.localStorage.setItem("margin_groups", groups.join(","));
  }
  globalThis.dispatchEvent(new globalThis.Event(AUTH_SESSION_UPDATED_EVENT));
};

const logout = (): void => {
  redirectToLoggedOut();
  if (typeof globalThis.dispatchEvent === "function") {
    globalThis.dispatchEvent(new globalThis.Event(AUTH_SESSION_UPDATED_EVENT));
  }
};

const signInAgain = (userId: string, groups: string): void => {
  if (hasOidcConfig()) {
    redirectToLogin("/");
    return;
  }
  if (typeof globalThis.localStorage !== "undefined") {
    globalThis.localStorage.setItem("margin_user", userId.trim() || "local-dev");
    globalThis.localStorage.setItem("margin_groups", groups.trim() || "architect");
  }
  if (typeof globalThis.dispatchEvent === "function") {
    globalThis.dispatchEvent(new globalThis.Event(AUTH_SESSION_UPDATED_EVENT));
  }
  if (typeof globalThis.window !== "undefined") {
    globalThis.window.location.assign("/");
  }
};

function RouteAccessDenied({ route }: { route: AppRoute }): React.JSX.Element {
  const allowedRoles = routeAllowedRoles(route);
  return (
    <section className="page-grid">
      <article className="card">
        <h2>Access denied</h2>
        <p>Your current role does not have access to this screen.</p>
        {allowedRoles ? <p className="subtle">Allowed roles: {allowedRoles.join(", ")}.</p> : null}
      </article>
    </section>
  );
}

function RouteNotFound(): React.JSX.Element {
  return (
    <section className="page-grid">
      <article className="card">
        <h2>Page not found</h2>
        <p className="subtle">
          This Margin screen does not exist, or the link points to a record that is no longer
          available.
        </p>
      </article>
    </section>
  );
}

export function App({ initialRoute = "/" }: AppProps): React.JSX.Element {
  const [domainScope, setDomainScope] = useState(() => {
    if (typeof globalThis.localStorage === "undefined") return "All domains";
    return globalThis.localStorage.getItem(DOMAIN_SCOPE_KEY) ?? "All domains";
  });
  const [authReady, setAuthReady] = useState(!isAuthAutoRedirectEnabled());
  const [localSignInUser, setLocalSignInUser] = useState("local-dev");
  const [localSignInGroups, setLocalSignInGroups] = useState("architect");
  const authSession = useAuthSession();
  const role = authSession.role;
  const { route, params, query, navigate } = useRouter(initialRoute);

  useEffect(() => {
    if (!isAuthAutoRedirectEnabled()) return;
    if (typeof globalThis.window === "undefined") return;
    if (window.location.pathname === "/logged-out") {
      setAuthReady(true);
      return;
    }

    let canceled = false;
    const ensureSession = async (): Promise<void> => {
      try {
        if (await completeOidcLogin()) {
          globalThis.dispatchEvent(new globalThis.Event(AUTH_SESSION_UPDATED_EVENT));
        }
        const token = globalThis.localStorage?.getItem("margin_access_token");
        if (!token && !canceled) {
          redirectToLogin();
          return;
        }
        const rbacResponse = await fetch("/api/rbac/me", {
          headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
        });
        if (rbacResponse.status === 401) {
          clearAuthSession();
          if (!canceled) redirectToLogin();
          return;
        }
        if (rbacResponse.ok) {
          const rbacContext = (await rbacResponse.json()) as unknown;
          applyRbacSession(rbacContext);
        }
        if (!canceled) setAuthReady(true);
      } catch {
        if (!canceled) {
          redirectToLogin();
        }
      }
    };

    void ensureSession();
    return () => {
      canceled = true;
    };
  }, []);

  const handleDomainScopeChange = (value: string): void => {
    setDomainScope(value);
    if (typeof globalThis.localStorage === "undefined") return;
    globalThis.localStorage.setItem(DOMAIN_SCOPE_KEY, value);
  };

  if (!authReady) {
    return (
      <section className="page-grid">
        <article className="card">
          <h2>Redirecting to Margin login...</h2>
          <p className="subtle">Please wait while we verify your session.</p>
        </article>
      </section>
    );
  }

  if (route === "/logged-out") {
    const oidcConfigured = hasOidcConfig();
    return (
      <section className="logged-out-page">
        <article className="logged-out-card">
          <div className="brand logged-out-brand">
            <span className="brand-mark" aria-hidden="true">
              NX
            </span>
            <div>
              <h1>Margin</h1>
              <span>Architecture Knowledge</span>
            </div>
          </div>
          <p className="eyebrow">Session Ended</p>
          <h2>You are logged out</h2>
          <p className="subtle">Your Margin session ended successfully.</p>
          {oidcConfigured ? (
            <button type="button" onClick={() => signInAgain(localSignInUser, localSignInGroups)}>
              Sign in again
            </button>
          ) : (
            <form
              className="stacked-form"
              onSubmit={(event) => {
                event.preventDefault();
                signInAgain(localSignInUser, localSignInGroups);
              }}
            >
              <label className="field">
                <span>User</span>
                <input
                  value={localSignInUser}
                  onChange={(event) => setLocalSignInUser(event.target.value)}
                />
              </label>
              <label className="field">
                <span>Groups</span>
                <input
                  value={localSignInGroups}
                  onChange={(event) => setLocalSignInGroups(event.target.value)}
                />
              </label>
              <button type="submit">Sign in again</button>
            </form>
          )}
        </article>
      </section>
    );
  }

  if (!isTestRuntime() && !authSession.isAuthenticated) {
    return (
      <section className="page-grid">
        <article className="card">
          <h2>Sign in required</h2>
          <p className="subtle">Your Margin session is missing or has ended.</p>
          <button type="button" onClick={() => redirectToLogin("/")}>
            Sign in
          </button>
        </article>
      </section>
    );
  }

  let content: React.JSX.Element = <HomePage domainScope={domainScope} onNavigate={navigate} />;
  if (route === "/") content = <HomePage domainScope={domainScope} onNavigate={navigate} />;
  if (route === "/not-found") content = <RouteNotFound />;
  if (route === "/agent" || route === "/agent/:runId") {
    content = <AgentPage route={route} params={params} onNavigate={navigate} />;
  }
  if (route === "/me") {
    content = <PersonalPage route={route} params={params} onNavigate={navigate} />;
  }
  if (route === "/ask" || route === "/ask/:threadId") {
    content = <AskPage params={params} query={query} onNavigate={navigate} />;
  }
  if (["/documents", "/documents/:sourceId"].includes(route)) {
    content = <ViewDocumentsPage params={params} onNavigate={navigate} />;
  }
  if (
    [
      "/sources",
      "/sources/upload",
      "/sources/:sourceId",
      "/sources/:sourceId/elements/:elementId",
    ].includes(route)
  ) {
    content = <SourcesPage params={params} onNavigate={navigate} />;
  }
  if (route === "/knowledge/entities") {
    content = <EntityCatalogPage onNavigate={navigate} />;
  }
  if (route === "/knowledge/entities/:entityId") {
    content = <EntityDetailPage entityId={params.entityId ?? ""} onNavigate={navigate} />;
  }
  if (route === "/knowledge/domains/:domainId/charter") {
    content = <DomainCharterPage route={route} params={params} onNavigate={navigate} />;
  }
  if (
    [
      "/knowledge/domains",
      "/knowledge/domains/:domainId",
      "/knowledge/ideablocks",
      "/knowledge/ideablocks/:ideaBlockId",
      "/knowledge/relationships",
      "/knowledge/relationships/entity/:entityId",
      "/knowledge/relationships/ideablock/:ideaBlockId",
      "/knowledge/relationships/governance/:recordId",
    ].includes(route)
  ) {
    content = <KnowledgePage route={route} params={params} onNavigate={navigate} />;
  }
  if (["/views/graph", "/views/graph/:entityId"].includes(route)) {
    content = <GraphPage route={route} params={params} onNavigate={navigate} />;
  }
  if (["/views/heatmap", "/views/c4", "/views/c4/:entityId"].includes(route)) {
    content = <ArchitectureViewsPage route={route} params={params} onNavigate={navigate} />;
  }
  if (route === "/architecture/wiki") {
    content = <ArchitectureWikiPage onNavigate={navigate} />;
  }
  if (
    [
      "/governance",
      "/governance/records/:recordId",
      "/governance/review",
      "/governance/review/:taskId",
    ].includes(route)
  ) {
    content = canAccessRoute(role, route) ? (
      <GovernancePage
        route={route}
        params={params}
        onNavigate={navigate}
        canManage={role !== "Auditor"}
      />
    ) : (
      <RouteAccessDenied route={route} />
    );
  }
  if (["/quality/lint", "/quality/evaluations", "/quality/evaluations/:runId"].includes(route)) {
    content = <QualityPage route={route} params={params} />;
  }
  if (["/admin/models", "/admin/access", "/admin/system"].includes(route)) {
    content = canAccessRoute(role, route) ? (
      <AdminPage route={route} />
    ) : (
      <RouteAccessDenied route={route} />
    );
  }

  return (
    <AppShell
      activeRoute={route}
      role={role}
      userId={authSession.userId}
      isAuthenticated={authSession.isAuthenticated}
      domainScope={domainScope}
      limitedByPermissions={hasPermissionLimitBanner(role)}
      onDomainScopeChange={handleDomainScopeChange}
      onRouteChange={navigate}
      onLogout={logout}
    >
      {content}
    </AppShell>
  );
}

const rootElement = typeof document !== "undefined" ? document.getElementById("root") : null;
if (rootElement) {
  ReactDOM.createRoot(rootElement).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}
