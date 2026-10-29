import React, { useMemo, useState } from "react";
import { GlobalSearchCommand } from "../search/GlobalSearchCommand";
import { AppRoute, Role, ROUTE_META } from "../../features/pages/types";
import { Breadcrumbs } from "./Breadcrumbs";
import { CollapseToggle } from "./CollapseToggle";
import { PermissionBanner } from "./PermissionBanner";
import { NAVIGATION } from "./navigation";

type AppShellProps = {
  activeRoute: AppRoute;
  role: Role;
  userId: string;
  isAuthenticated: boolean;
  domainScope: string;
  limitedByPermissions: boolean;
  onDomainScopeChange: (_value: string) => void;
  onRouteChange: (_route: AppRoute, _params?: Record<string, string>) => void;
  onLogout: () => void;
  children: React.ReactNode;
};

function toBreadcrumbs(route: AppRoute): string[] {
  if (route === "/") return ["Home"];
  const parts = route.split("/").filter(Boolean);
  const section = parts[0]?.replace(/-/g, " ");
  const sectionLabel = section ? section.charAt(0).toUpperCase() + section.slice(1) : "Margin";
  const current = ROUTE_META[route].title;
  return sectionLabel === current ? [current] : [sectionLabel, current];
}

export function AppShell({
  activeRoute,
  role,
  userId,
  isAuthenticated,
  domainScope: _domainScope,
  limitedByPermissions,
  onDomainScopeChange: _onDomainScopeChange,
  onRouteChange,
  onLogout,
  children,
}: AppShellProps): React.JSX.Element {
  const [navOpen, setNavOpen] = useState(true);
  const sections = useMemo(
    () =>
      NAVIGATION.map((section) => ({
        ...section,
        items: section.items.filter((item) => !item.roles || item.roles.includes(role)),
      })),
    [role]
  );

  return (
    <div className={navOpen ? "app-shell" : "app-shell nav-collapsed"}>
      <aside>
        <div className="nav-header">
          {navOpen ? (
            <div className="brand">
              <span className="brand-mark" aria-hidden="true">
                NX
              </span>
              <div>
                <h1>Margin</h1>
                <span>Architecture Knowledge</span>
              </div>
            </div>
          ) : null}
          <CollapseToggle
            className="nav-toggle"
            expanded={navOpen}
            expandedLabel="Collapse menu bar"
            collapsedLabel="Expand menu bar"
            expandedDirection="left"
            collapsedDirection="right"
            onClick={() => setNavOpen((value) => !value)}
          />
        </div>
        {navOpen
          ? sections.map((section) => (
              <section className="nav-section" key={section.label}>
                <h2>{section.label}</h2>
                <ul>
                  {section.items.map((item) => (
                    <li key={item.route}>
                      <button
                        className={item.route === activeRoute ? "nav-link active" : "nav-link"}
                        onClick={() => onRouteChange(item.route)}
                        type="button"
                        aria-current={item.route === activeRoute ? "page" : undefined}
                      >
                        <span className="nav-dot" aria-hidden="true" />
                        {item.label}
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))
          : null}
      </aside>
      <main>
        <header className="app-header">
          <div className="header-left">
            <GlobalSearchCommand onNavigate={onRouteChange} />
          </div>
          <div className="header-right">
            <div className="environment-banner">
              <span>Local</span>
              <strong>{role}</strong>
              <span>{userId}</span>
              <em>{isAuthenticated ? "OIDC" : "Missing token"}</em>
            </div>
            <button className="logout-button" onClick={onLogout} type="button">
              Log out
            </button>
          </div>
        </header>
        <Breadcrumbs items={toBreadcrumbs(activeRoute)} />
        <PermissionBanner limitedByPermissions={limitedByPermissions} />
        {children}
      </main>
    </div>
  );
}
