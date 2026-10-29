import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { App } from "./main";
import { AppRoute } from "./features/pages/types";

const renderApp = (initialRoute?: AppRoute): string =>
  renderToStaticMarkup(React.createElement(App, initialRoute ? { initialRoute } : undefined));
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

const renderAppAs = (groups: string, initialRoute?: AppRoute): string => {
  setLocalStorage({
    margin_access_token: "header.payload.signature",
    margin_groups: groups,
  });
  return renderApp(initialRoute);
};

describe("Margin frontend shell", () => {
  it("renders information architecture navigation", () => {
    const markup = renderAppAs("platform_admin");
    expect(markup).toContain("Sources");
    expect(markup).toContain("Knowledge");
    expect(markup).toContain("Ask Margin");
    expect(markup).toContain("Entities &amp; Vocabulary");
    expect(markup).toContain("Reviews");
    expect(markup).toContain("Architecture Governance");
    expect(markup).toContain("Administration");
  });

  it("renders home dashboard and shared shell", () => {
    const markup = renderApp();
    expect(markup).toContain("Search authorized knowledge");
    expect(markup).toContain('class="environment-banner"');
    expect(markup).toContain("Local");
    expect(markup).toContain("Knowledge state summary");
    expect(markup).toContain("Domain health");
    expect(markup).toContain("My review tasks");
  });

  it("renders model administration surface", () => {
    const markup = renderAppAs("platform_admin", "/admin/models");
    expect(markup).toContain("Model administration");
    expect(markup).toContain('aria-label="Create model"');
    expect(markup).toContain("Model operations");
    expect(markup).toContain("Loading model registry...");
  });

  it("renders the entity catalog and dedicated access/system admin pages", () => {
    const entityCatalogMarkup = renderAppAs("platform_admin", "/knowledge/entities");
    const accessMarkup = renderAppAs("platform_admin", "/admin/access");
    const systemMarkup = renderAppAs("platform_admin", "/admin/system");

    expect(entityCatalogMarkup).toContain("Entity Catalog");
    expect(accessMarkup).toContain("Users and access");
    expect(systemMarkup).toContain("System health checks");
  });
});
